"""Run a bounded laboratory soak and record local UA reads and process resources."""
from __future__ import annotations

import argparse
import asyncio
import csv
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def _metrics_command(pid: int) -> str:
    """Build a PowerShell query for the real server child behind Windows venv launchers."""
    return (
        f"$c=Get-CimInstance Win32_Process | Where-Object {{ $_.ParentProcessId -eq {pid} "
        "-and $_.CommandLine -like '*-m opc_da_bridge.lab.server*' } | Select-Object -First 1; "
        "if (-not $c) { throw 'gateway Python child process not found' }; "
        "$p=Get-Process -Id $c.ProcessId -ErrorAction Stop; "
        "'{0},{1:F3},{2},{3},{4}' -f $c.ProcessId,$p.CPU,$p.WorkingSet64,$p.HandleCount,$p.Responding"
    )


def _process_metrics(pid: int) -> str:
    """Return CPU seconds, working-set bytes, handles and responding state on Windows."""
    if os.name != "nt":
        return "unsupported,unsupported,unsupported,unsupported,unsupported"
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _metrics_command(pid)],
        check=True, capture_output=True, text=True, timeout=10,
    )
    return result.stdout.strip()


async def run(config_file: Path, duration_s: float, sample_interval_s: float) -> int:
    try:
        from asyncua import Client
    except ImportError as exc:
        raise SystemExit("Install the lab extra before running stability checks") from exc

    cfg = json.loads(config_file.read_text(encoding="utf-8"))
    if cfg["opc_ua"].get("security", {}).get("mode", "local_no_security") != "local_no_security":
        raise SystemExit("stability.py currently verifies the local loopback NoSecurity endpoint only")
    host = cfg["opc_ua"]["host"]
    import ipaddress
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host.lower() == "localhost"
    if not loopback:
        raise SystemExit("stability client is restricted to a local loopback endpoint")
    endpoint = f"opc.tcp://{host}:{cfg['opc_ua']['port']}/opc-da-bridge/lab/"
    namespace_uri = cfg["opc_ua"]["namespace_uri"]
    run_dir = Path(cfg.get("stability", {}).get("output_dir", "stability-results"))
    run_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    event_path = run_dir / f"soak-{stamp}.log"
    reads_path = run_dir / f"soak-{stamp}-ua.csv"
    metrics_path = run_dir / f"soak-{stamp}-process.csv"
    logger = logging.getLogger("opc_da_bridge.lab.stability")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    handler = logging.FileHandler(event_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.addHandler(logging.StreamHandler())

    root = Path(__file__).resolve().parents[3]
    gateway_log = run_dir / f"gateway-{stamp}.log"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root / "src") + os.pathsep + env.get("PYTHONPATH", "")
    gateway = subprocess.Popen(
        [sys.executable, "-m", "opc_da_bridge.lab.server", "--config", str(config_file.resolve())],
        cwd=root, env=env, stdin=subprocess.DEVNULL,
        stdout=gateway_log.open("a", encoding="utf-8"), stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0,
    )
    start = time.monotonic()
    logger.info("soak started duration_s=%.1f sample_interval_s=%.1f gateway_pid=%d", duration_s,
                sample_interval_s, gateway.pid)
    failed = False
    expected_types: dict[str, str] = {}
    last_timestamps: dict[str, datetime] = {}
    last_metrics = 0.0
    client = Client(url=endpoint, timeout=5)
    try:
        with reads_path.open("w", newline="", encoding="utf-8") as rf, metrics_path.open(
            "w", newline="", encoding="utf-8"
        ) as mf:
            reads = csv.writer(rf)
            metrics = csv.writer(mf)
            reads.writerow(["utc", "item_id", "value", "python_type", "variant_type", "status", "source_timestamp_utc", "check"])
            metrics.writerow(["utc", "elapsed_s", "launcher_pid", "process_state", "server_pid", "cpu_seconds", "working_set_bytes", "handles", "responding"])
            deadline = min(start + duration_s, time.monotonic() + 120)
            while time.monotonic() < deadline:
                try:
                    await client.connect()
                    break
                except Exception as exc:
                    logger.warning("waiting for local UA endpoint: %s", exc)
                    await asyncio.sleep(2)
            else:
                failed = True
                raise RuntimeError("local UA endpoint did not become ready within 120 seconds")
            try:
                ns = await client.get_namespace_index(namespace_uri)
                nodes = [(tag["item_id"], client.get_node(f"ns={ns};s={tag['node_id']}")) for tag in cfg["tags"]]
                if len(nodes) != 2:
                    raise SystemExit("soak configuration must contain exactly the two approved laboratory tags")
                ready_deadline = min(start + 120, time.monotonic() + 120)
                while True:
                    ready = True
                    for _, node in nodes:
                        try:
                            initial = await node.read_data_value()
                            if int(initial.StatusCode.value) & 0xC0000000 or initial.Value.Value is None:
                                ready = False
                        except Exception:
                            ready = False
                    if ready:
                        logger.info("both configured UA nodes reached a first Good sample")
                        break
                    if time.monotonic() >= ready_deadline:
                        failed = True
                        raise RuntimeError("both UA tags did not reach Good status within 120 seconds")
                    await asyncio.sleep(1)
                while time.monotonic() - start < duration_s:
                    now_utc = datetime.now(timezone.utc)
                    elapsed = time.monotonic() - start
                    process_state = "running" if gateway.poll() is None else f"exited:{gateway.returncode}"
                    if process_state != "running":
                        failed = True
                        logger.error("gateway process exited early with code %s", gateway.returncode)
                    if time.monotonic() - last_metrics >= 30:
                        try:
                            parts = _process_metrics(gateway.pid).split(",")
                            metrics.writerow([now_utc.isoformat(), f"{elapsed:.1f}", gateway.pid, process_state, *parts])
                        except Exception as exc:
                            metrics.writerow([now_utc.isoformat(), f"{elapsed:.1f}", gateway.pid,
                                              "metrics_error", "", "", "", "", str(exc)])
                            failed = True
                            logger.error("process metrics failed: %s", exc)
                        mf.flush()
                        last_metrics = time.monotonic()
                        logger.info("progress elapsed_s=%.0f remaining_s=%.0f gateway_state=%s", elapsed,
                                    max(0, duration_s-elapsed), process_state)
                    for item_id, node in nodes:
                        check = "OK"
                        try:
                            dv = await node.read_data_value()
                            value = dv.Value.Value
                            variant_type = dv.Value.VariantType.name
                            python_type = type(value).__name__
                            status = str(dv.StatusCode)
                            ts = dv.SourceTimestamp
                            if ts is None:
                                check = "MISSING_SOURCE_TIMESTAMP"
                            elif ts.tzinfo is None:
                                check = "NAIVE_SOURCE_TIMESTAMP"
                            else:
                                ts = ts.astimezone(timezone.utc)
                                if (ts - now_utc).total_seconds() > 5:
                                    check = "FUTURE_SOURCE_TIMESTAMP"
                                prev = last_timestamps.get(item_id)
                                if prev and ts < prev:
                                    check = "SOURCE_TIMESTAMP_MOVED_BACKWARD"
                                last_timestamps[item_id] = ts
                            prior = expected_types.setdefault(item_id, variant_type)
                            if prior != variant_type:
                                check = "VARIANT_TYPE_CHANGED"
                            status_value = int(dv.StatusCode.value)
                            if status_value & 0xC0000000:
                                check = "NON_GOOD_STATUS"
                            reads.writerow([now_utc.isoformat(), item_id, repr(value), python_type, variant_type,
                                            status, ts.isoformat() if ts else "", check])
                            if check not in {"OK", "MISSING_SOURCE_TIMESTAMP"}:
                                failed = True
                                logger.error("UA check %s item=%s status=%s type=%s source_ts=%s", check,
                                             item_id, status, variant_type, ts)
                        except Exception as exc:
                            failed = True
                            logger.error("UA read failed item=%s: %s", item_id, exc)
                            reads.writerow([now_utc.isoformat(), item_id, "", "", "", "", "",
                                            f"READ_ERROR:{type(exc).__name__}"])
                        rf.flush()
                    await asyncio.sleep(sample_interval_s)
            finally:
                try:
                    await client.disconnect()
                except Exception:
                    pass
    except Exception as exc:
        failed = True
        logger.error("soak interrupted: %s", exc)
    effective = time.monotonic() - start
    if gateway.poll() is None:
        try:
            if os.name == "nt":
                gateway.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                gateway.terminate()
        except OSError:
            pass
        try:
            gateway.wait(timeout=15)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                subprocess.run(["taskkill.exe", "/PID", str(gateway.pid), "/T", "/F"],
                               check=False, capture_output=True, timeout=15)
            else:
                gateway.kill()
            gateway.wait(timeout=15)
    logger.info("soak finished effective_s=%.1f gateway_returncode=%s result=%s", effective,
                gateway.returncode, "CHECK_FAILURES" if failed else "NO_CHECK_FAILURES")
    logger.info("artifacts events=%s ua_reads=%s process_metrics=%s gateway_log=%s", event_path, reads_path,
                metrics_path, gateway_log)
    return 2 if failed else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a bounded OPC DA to UA laboratory soak")
    parser.add_argument("--config", default="lab-config.json")
    parser.add_argument("--duration-s", type=float, default=3600)
    parser.add_argument("--sample-interval-s", type=float, default=15)
    args = parser.parse_args()
    if args.duration_s <= 0 or args.sample_interval_s <= 0:
        raise SystemExit("duration and sample interval must be positive")
    raise SystemExit(asyncio.run(run(Path(args.config), args.duration_s, args.sample_interval_s)))


if __name__ == "__main__":
    main()
