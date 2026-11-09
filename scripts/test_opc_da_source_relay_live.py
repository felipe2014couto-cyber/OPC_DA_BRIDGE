#!/usr/bin/env python3
"""Opt-in, read-only live source relay test using an isolated temporary sink.

This test only reads the explicitly provisioned Rust DA laboratory snapshot.
It rejects a generic COM transport to avoid activating another LocalServer.
No persistent management database or Windows destination is modified.
"""
# SPDX-License-Identifier: MIT
from __future__ import annotations

import argparse
import copy
import json
import tempfile
import time
from pathlib import Path

from opc_da_bridge.management.deployment import Deployments, export_project, fingerprint
from opc_da_bridge.management.sources import Sources, SshSourceChannel
from opc_da_bridge.management.store import Store

PROG_ID = "RustDaBridge.Lab.1"
CLSID = "{7A2F45B1-64D3-4E8C-9A11-3C852B7F2140}"
ITEMS = (
    ("RB1PI1.TESTE.TESTE1.TESTE2", "DiscoveryLab.Number", "Double"),
    ("RB1PI1.TESTE.TESTE1.DIGITAL", "DiscoveryLab.Boolean", "Boolean"),
)


class MemorySink:
    def __init__(self):
        self.batches = []

    def runtime(self, _target, payload):
        header = {k: payload[k] for k in ("machine_id", "configuration_revision", "config_sha256", "source_module_id")}
        if payload["operation"] == "source-session":
            return dict(header, ok=True, pid=9001, session_id=1)
        self.batches.append(copy.deepcopy(payload))
        all_good = all(sample["hresult"] >= 0 and sample["quality"] & 192 == 192 for sample in payload["samples"])
        return dict(header, ok=True, pid=9001, sequence=payload["sequence"], all_good=all_good)


def add(store, kind, data):
    old = {row[0] for row in store.db.execute("SELECT id FROM nodes")}
    store.mutate("create", kind, data, store.revision())
    return next(row[0] for row in store.db.execute("SELECT id FROM nodes") if row[0] not in old)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, help="Host/IP already provisioned in the private source-agents.json")
    parser.add_argument("--duration-s", type=int, default=30, help="Test duration; default 30s")
    parser.add_argument("--interval-s", type=int, default=5, help="Polling interval; minimum 5s")
    args = parser.parse_args()
    if args.duration_s < 15 or not 5 <= args.interval_s <= 60:
        parser.error("use duration-s >= 15 and interval-s between 5 and 60")

    repo = Path(__file__).resolve().parents[1]
    provision_path = repo / ".opc-bridge-management" / "source-agents.json"
    provision = json.loads(provision_path.read_text(encoding="utf-8"))["sources"].get(args.host)
    if not provision or provision.get("enabled") is not True or provision.get("host") != args.host:
        parser.error("host is not explicitly provisioned")
    if provision.get("read_transport") != "rust_lab_loopback_snapshot":
        parser.error("live test requires explicit rust_lab_loopback_snapshot; refusing COM activation")

    temp_root = repo / ".offline" / "discovery-tests"
    temp_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="source-relay-live-", dir=temp_root) as temp:
        data = Path(temp)
        store = Store(data)
        sink = MemorySink()
        deployments = Deployments(store, sink)
        sources = Sources(store, deployments, SshSourceChannel(data))
        (data / "source-agents.json").write_text(json.dumps({"schema_version": 1, "sources": {args.host: provision}}), encoding="utf-8")
        (data / "source-agents.json").chmod(0o600)
        try:
            discovery = sources.discover(args.host)
            server = next((item for item in discovery["servers"] if item["prog_id"] == PROG_ID), None)
            if server is None or server.get("clsid") != CLSID:
                raise RuntimeError("explicit Rust DA laboratory ProgID/CLSID was not discovered")

            connection = add(store, "connections", {"name": "Temporary live source test"})
            machine_id = add(store, "machines", {"parent_id": connection, "name": "Temporary sink", "host": "sink.invalid"})
            module_data = {
                "name": "Rust DA snapshot source", "source_kind": "opc_da", "source_host": args.host,
                "source_progid": PROG_ID, "source_clsid": CLSID,
                "source_discovery_id": discovery["discovery_id"], "update_interval_s": args.interval_s,
            }
            sources.validate_selection(module_data)
            module_id = add(store, "modules", dict(module_data, parent_id=machine_id))
            folder_id = add(store, "folders", {"parent_id": module_id, "name": "Two simulated values"})
            for source_id, item_id, data_type in ITEMS:
                add(store, "variables", {
                    "parent_id": folder_id, "name": item_id.rsplit(".", 1)[-1],
                    "source_id": source_id, "item_id": item_id, "data_type": data_type,
                    "initial_value": 0.0 if data_type == "Double" else False,
                })
            (data / "agents.json").write_text(json.dumps({"schema_version": 1, "targets": {
                machine_id: {"host": "sink.invalid", "expected_hostname": "SINK"}
            }}), encoding="utf-8")
            state, machine = deployments.machine(machine_id)
            envelope = export_project(state, machine_id, deployments.target(machine))
            deployments.records[machine_id] = {
                "status": "applied", "fingerprint": fingerprint(machine),
                "receipt": {"configuration_revision": state["revision"], "project_sha256": envelope["project_sha256"]},
            }

            sources.start()
            time.sleep(args.duration_s)
            sources.close()
            batches = [batch for batch in sink.batches if batch["operation"] == "source-samples"]
            status = sources.status(machine_id)
            valid = all(
                [sample["value"] for sample in batch["samples"]] == [7.0, False]
                and all(sample["quality"] == 192 and sample["hresult"] == 0 for sample in batch["samples"])
                and all(sample["source_timestamp_filetime"] for sample in batch["samples"])
                for batch in batches
            )
            summary = {
                "poll_batches": len(batches), "interval_s": args.interval_s,
                "status": status["status"], "origin": status["origins"][0],
                "last_samples": batches[-1]["samples"] if batches else [],
            }
            print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
            if len(batches) < 2 or not valid or status["status"] != "connected":
                raise RuntimeError("scheduled live source relay did not satisfy expected simulated samples")
            print("LIVE_SOURCE_RELAY_PASS")
        finally:
            sources.close()
            store.close()


if __name__ == "__main__":
    main()
