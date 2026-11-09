#!/usr/bin/env python3
"""Bounded opt-in benchmark: existing simulated OPC module -> real DA destination.

Pauses only the selected relay module, restores its operator control exactly,
never changes the database/catalog/COM registration. Artifacts must already be
staged and verified. Reports OPC samples privately, never credentials.
"""
# SPDX-License-Identifier: MIT
from __future__ import annotations
import argparse
import copy
import json
import os
from pathlib import Path
import time
import urllib.request
import uuid

from opc_da_bridge.management.deployment import SshLabChannel
from opc_da_bridge.management.sources import Sources, SshSourceChannel
from measure_relay_timing import distribution


def private_file(path, content):
    with open(path, "xb") as file:
        os.chmod(path, 0o600)
        file.write(content)


def replace_control(path, expected, content):
    temporary = path.with_name(".benchmark-control-" + uuid.uuid4().hex)
    try:
        private_file(temporary, content)
        if path.read_bytes() != expected:
            raise RuntimeError("Operator control changed concurrently; no overwrite; restore from backup after inspection")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--machine-id", type=uuid.UUID, required=True)
    parser.add_argument("--module-id", type=uuid.UUID, required=True)
    parser.add_argument("--source-artifact", type=Path, required=True)
    parser.add_argument("--destination-artifact", type=Path, required=True)
    parser.add_argument("--interval-s", type=float, choices=(1, .5), required=True)
    parser.add_argument("--duration-s", type=int, default=60)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if not 15 <= args.duration_s <= 120 or not 1 <= args.port <= 65535:
        parser.error("duration must be 15..120s and port valid")
    # Reserve before remote operations; an existing report is never overwritten.
    private_file(args.output, b"")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def api(route):
        with opener.open(f"http://127.0.0.1:{args.port}" + route, timeout=3) as response:
            return json.load(response)
    mid, module_id = str(args.machine_id), str(args.module_id)
    data = args.data_dir
    source_artifact = json.loads(args.source_artifact.read_text())
    if source_artifact.get("test_scope") != "simulated_tags_only":
        parser.error("source artifact must explicitly authorize simulated tags only")
    state = api("/api/v1/state")
    machine = next(m for c in state["connections"] for m in c["machines"] if m["id"] == mid)
    module = next(m for m in machine["modules"] if m["id"] == module_id)
    variables = [v for f in module["folders"] for v in f["variables"]]
    if not variables or set(source_artifact["allowed_items"]) != {v["source_id"] for v in variables}:
        parser.error("selected catalog differs from the simulated artifact allowlist")
    receipt = api(f"/api/v1/machines/{mid}/deployment")["deployment"]
    if receipt["status"] != "applied":
        parser.error("destination catalog is not confirmed applied")
    header = {"machine_id": mid, "source_module_id": module_id,
              "configuration_revision": receipt["receipt"]["configuration_revision"],
              "config_sha256": receipt["receipt"]["project_sha256"]}
    source = copy.deepcopy(json.loads((data / "source-agents.json").read_text())["sources"][module["source_host"]])
    if source.get("read_transport") != "opc_da_automation" or source.get("enabled") is not True:
        parser.error("source is not provisioned for Automation")
    source.update(read_session_transport="ssh_stream_v1", stream_agent_dir=source_artifact["remote_dir"])
    destination = copy.deepcopy(json.loads((data / "agents.json").read_text())["targets"][mid])
    destination.update(runtime_transport="ssh_stream_v1", stream_agent_dir=json.loads(args.destination_artifact.read_text())["remote_dir"])
    request = {"operation": "read", "prog_id": module["source_progid"], "clsid": module["source_clsid"],
               "items": [Sources._source_item_request(v) for v in variables]}
    path = data / "relay-read-windows.json"
    original = path.read_bytes(); controls = json.loads(original)
    backup = args.output.with_suffix(".control-before.json")
    private_file(backup, original)
    controls["windows"][module_id] = {"machine_id": mid, "mode": "continuous", "enabled": False, "interval_s": 5}
    paused = json.dumps(controls, indent=2).encode()
    source_channel, destination_channel = SshSourceChannel(data), SshLabChannel(data)
    rows = []; warmup = {}; failures = []; restored = False
    changed_control = False
    try:
        replace_control(path, original, paused); changed_control = True
        deadline = time.monotonic() + 30
        while True:
            status = api(f"/api/v1/machines/{mid}/source-status")
            if next(s for s in status["origins"] if s["module_id"] == module_id)["status"] == "paused":
                break
            if time.monotonic() > deadline: raise RuntimeError("existing relay did not acknowledge pause")
            time.sleep(.2)
        # Warmup is explicitly measured, not silently dropped from results.
        start = time.monotonic()
        reply = source_channel.request(source, request); Sources.verify(source, reply)
        warmup["source_ms"] = (time.monotonic() - start) * 1000
        Sources._validate_opc_samples(object.__new__(Sources), variables, reply["samples"], "opc_da_automation")
        start = time.monotonic()
        ack = destination_channel.runtime(destination, dict(header, operation="source-session"))
        warmup["destination_session_ms"] = (time.monotonic() - start) * 1000
        if any(ack.get(k) != v for k, v in header.items()): raise RuntimeError("session header mismatch")
        session_id = ack["session_id"]; server_pid = ack["pid"]
        sequence = 0; start = time.monotonic(); scheduled = start; skipped = 0
        while time.monotonic() - start < args.duration_s:
            time.sleep(max(0, scheduled - time.monotonic()))
            if time.monotonic() - start >= args.duration_s: break
            began = time.monotonic()
            reply = source_channel.request(source, request); Sources.verify(source, reply)
            samples = Sources._validate_opc_samples(object.__new__(Sources), variables, reply["samples"], "opc_da_automation")
            read = time.monotonic()
            sequence += 1
            ack = destination_channel.runtime(destination, dict(header, operation="source-samples", session_id=session_id,
                                                                sequence=sequence, samples=samples))
            ended = time.monotonic()
            if ack.get("pid") != server_pid or ack.get("sequence") != sequence or any(ack.get(k) != v for k, v in header.items()):
                raise RuntimeError("runtime identity/sequence changed")
            rows.append({"cycle": sequence, "started_s": began-start, "acked_s": ended-start,
                         "start_lateness_ms": (began-scheduled)*1000, "source_ms": (read-began)*1000,
                         "delivery_ms": (ended-read)*1000, "total_ms": (ended-began)*1000,
                         "source_pid": reply.get("source_pid"), "source_timing_ms": reply.get("source_timing_ms"),
                         "destination_pid": server_pid, "source_handles": [s.get("source_server_handle") for s in samples],
                         "all_good": ack.get("all_good"), "samples": samples})
            scheduled += args.interval_s
            if scheduled < ended:
                missed = int((ended - scheduled) // args.interval_s) + 1
                skipped += missed; scheduled += missed * args.interval_s
        summary = {k: distribution([r[k] for r in rows]) for k in ("source_ms", "delivery_ms", "total_ms", "start_lateness_ms")}
        summary["source_start_interval_s"] = distribution([b["started_s"]-a["started_s"] for a,b in zip(rows,rows[1:])])
        summary["destination_ack_interval_s"] = distribution([b["acked_s"]-a["acked_s"] for a,b in zip(rows,rows[1:])])
        summary.update(cycles=len(rows), skipped_slots=skipped, overrun_cycles=sum(r["total_ms"] > args.interval_s*1000 for r in rows),
                       degraded_cycles=sum(r["all_good"] is not True for r in rows), effective_duration_s=time.monotonic()-start,
                       source_pids=sorted({r["source_pid"] for r in rows}), destination_pid=server_pid)
        print(json.dumps(summary, indent=2), flush=True)
    except Exception as failure:
        # Fixed class only: remote errors may include private paths/details.
        failures.append(type(failure).__name__)
        raise
    finally:
        source_channel.close(); destination_channel.close()
        if changed_control:
            replace_control(path, paused, original); restored = True
        report = {"schema_version": 1, "configuration_revision": state["revision"], "interval_s": args.interval_s,
                  "warmup": warmup, "summary": locals().get("summary"), "failures": failures,
                  "control_restored": restored, "control_backup": str(backup), "rows": rows,
                  "limitations": "Monotonic timings on Linux. Destination ACK is not a PI Client read. "
                                  "First OPC read and transport startup measured separately as warmup. "
                                  "No GUI or destination COM client validation in this script."}
        args.output.write_text(json.dumps(report, indent=2, allow_nan=False))


if __name__ == "__main__": main()
