#!/usr/bin/env python3
"""Observe an existing relay through its local API; never start an OPC poller.

No values, Item IDs, credentials or error text are stored. API observations
are not source sampling times and can miss intermediate cycles.
"""
# SPDX-License-Identifier: MIT
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import time
import urllib.request
import uuid


def distribution(values):
    values = sorted(values)
    if not values:
        return {"count": 0}
    return {"count": len(values), "mean": statistics.mean(values),
            "median": statistics.median(values),
            "p95_nearest_rank": values[math.ceil(.95 * len(values)) - 1],
            "max": values[-1]}


class Observer:
    def __init__(self):
        self.last = {}
        self.rows = []
        self.baselines = set()

    def observe(self, status, elapsed_s):
        for origin in status["origins"]:
            module = origin["module_id"]
            stamp = origin.get("checked_at")
            if not stamp or self.last.get(module) == stamp:
                continue
            baseline = module not in self.last
            self.last[module] = stamp
            timings = {k: v for k, v in origin.get("timings_ms", {}).items()
                       if k in ("session", "source_read", "delivery", "total")
                       and type(v) in (int, float) and math.isfinite(v) and v >= 0}
            self.rows.append({"module_id": module, "observed_elapsed_s": elapsed_s,
                              "initial_cached_observation": baseline,
                              "status": origin["status"], "server_pid": origin.get("server_pid"),
                              "timings_ms": timings})

    def summary(self):
        result = {}
        for module in self.last:
            # Initial status may predate the measurement window; exclude it.
            rows = [r for r in self.rows if r["module_id"] == module
                    and not r["initial_cached_observation"]]
            intervals = [b["observed_elapsed_s"] - a["observed_elapsed_s"]
                         for a, b in zip(rows, rows[1:])]
            result[module] = {
                "observed_updates": len(rows),
                "non_connected_updates": sum(r["status"] != "connected" for r in rows),
                "observed_update_interval_s": distribution(intervals),
                "timings_ms": {key: distribution([r["timings_ms"][key] for r in rows
                                                    if key in r["timings_ms"]])
                               for key in ("session", "source_read", "delivery", "total")},
                "server_pids": sorted({r["server_pid"] for r in rows if r["server_pid"] is not None}),
                "missed_cycles": None, "late_cycles": None,
            }
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--machine-id", type=uuid.UUID, required=True)
    parser.add_argument("--duration-s", type=float, default=120)
    parser.add_argument("--observe-interval-s", type=float, default=.1)
    parser.add_argument("--output", required=True, help="New private JSON file; never overwritten")
    args = parser.parse_args()
    if not (1 <= args.port <= 65535 and 1 <= args.duration_s <= 3600
            and .05 <= args.observe_interval_s <= 5):
        parser.error("invalid port, duration (1..3600s) or observation interval (.05..5s)")
    # Reserve before collection, refuse existing files and bypass proxies only
    # for this explicitly loopback-only request. No global environment changes.
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as output:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        url = f"http://127.0.0.1:{args.port}/api/v1/machines/{args.machine_id}/source-status"
        observer = Observer()
        errors = 0
        gaps = []
        start = time.monotonic()
        previous = start
        while time.monotonic() - start < args.duration_s:
            try:
                with opener.open(url, timeout=2) as response:
                    body = response.read(2097153)
                if len(body) > 2097152:
                    raise ValueError("oversized status")
                now = time.monotonic()
                gaps.append(now - previous)
                previous = now
                observer.observe(json.loads(body), now - start)
            except (OSError, ValueError, KeyError, TypeError):
                errors += 1
            time.sleep(min(args.observe_interval_s, max(0, args.duration_s - (time.monotonic() - start))))
        report = {"schema_version": 1, "measurement": "passive_api_observation",
                  "duration_s": time.monotonic() - start, "api_errors": errors,
                  "observation_gap_s": distribution(gaps), "modules": observer.summary(),
                  "limitations": "No OPC/GUI test. Intervals observed on Linux, not source acquisition times. "
                                  "Initial cache excluded; intermediate cycles and failures may be missed. "
                                  "API state changes need not be successful delivery acknowledgements.",
                  "observations": observer.rows}
        json.dump(report, output, indent=2, allow_nan=False)
        output.write("\n")
        print(json.dumps({k: v for k, v in report.items() if k != "observations"}, indent=2))


if __name__ == "__main__":
    main()
