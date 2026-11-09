"""Deployment isolation and confirmation tests. Fake destinations, no SSH/COM."""
import copy
import hashlib
import json
import subprocess
import pytest
from opc_da_bridge.management.store import Store, ManagementError
from opc_da_bridge.management.deployment import Deployments, SshLabChannel, AgentError, RuntimeNotRunning, export_project


class Destinations:
    def __init__(self): self.applied = {}; self.invalid_identity = False; self.fail_apply = False
    def identify(self, target):
        mid = target.get("machine_id")
        res = {"ok": True, "machine_id": mid, "identity": {
            "hostname": "wrong-host" if self.invalid_identity else target["expected_hostname"], "addresses": [target["host"]]}}
        app = self.applied.get(mid) or self.applied.get(str(mid))
        if app:
            res["receipt"] = app[1].get("receipt")
            res["snapshot"] = app[1].get("snapshot")
        return res
    def apply(self, target, envelope):
        if self.fail_apply: raise AgentError("Rejected; previous configuration retained")
        p = json.loads(envelope["project_json"])
        assert hashlib.sha256(envelope["project_json"].encode()).hexdigest() == envelope["project_sha256"]
        values = [v for c in p["connections"] for m in c["modules"] for v in m["variables"]]
        response = self.identify(target)
        response.update(receipt={"machine_id": envelope["machine_id"], "project_sha256": envelope["project_sha256"], "configuration_revision": envelope["configuration_revision"], "hostname": target["expected_hostname"], "address": target["host"], "variable_count": len(values)}, snapshot={"pid": 123, "machine_id": envelope["machine_id"], "config_sha256": envelope["project_sha256"], "configuration_revision": envelope["configuration_revision"], "samples": [{"item_id": v["item_id"], "data_type": v["data_type"], "value": v["initial_value"], "quality": 192, "source_timestamp_filetime": 134383700000000000} for v in values]})
        self.applied[envelope["machine_id"]] = (copy.deepcopy(envelope), response)
        self.applied[str(envelope["machine_id"])] = (copy.deepcopy(envelope), response)
        return response
    def snapshot(self, target):
        mid = target.get("machine_id")
        app = self.applied.get(mid) or self.applied.get(str(mid))
        return app[1]


def setup(tmp_path):
    s = Store(tmp_path); destinations = Destinations(); d = Deployments(s, destinations); s.deployments = d
    def add(kind, data):
        before = {r[0] for r in s.db.execute("SELECT id FROM nodes")}
        s.mutate("create", kind, data, s.revision())
        return next(r[0] for r in s.db.execute("SELECT id FROM nodes") if r[0] not in before)
    c = add("connections", {"name": "Lab"}); machines = []; variables = []
    for i in (1, 2):
        m = add("machines", {"parent_id": c, "name": "Destination%d" % i, "host": "host%d.invalid" % i})
        module = add("modules", {"parent_id": m, "name": "Origin"})
        folder = add("folders", {"parent_id": module, "name": "Folder"})
        v = add("variables", {"parent_id": folder, "name": "Value", "data_type": "Double", "initial_value": i * 10})
        machines.append(m); variables.append(v)
    targets = {m: {"host": "host%d.invalid" % (i+1), "expected_hostname": "HOST%d" % (i+1), "machine_id": m} for i, m in enumerate(machines)}
    (tmp_path / "agents.json").write_text(json.dumps({"schema_version": 1, "targets": targets}))
    return s, d, destinations, machines, variables


def apply(s, d, id):
    response = d.start(id, s.revision()); assert response["deployment"]["status"] in ("applying", "applied")
    d.jobs[id].join(5); assert not d.jobs[id].is_alive()
    return d.status(d.machine(id)[1])


def test_two_destinations_are_isolated_and_confirmed(tmp_path):
    s,d,remote,machines,variables = setup(tmp_path)
    try:
        for id in machines: assert apply(s,d,id)["status"] == "applied"
        ids = []
        for id in machines:
            envelope, response = remote.applied[id]
            catalog = [v["item_id"] for c in json.loads(envelope["project_json"])["connections"] for m in c["modules"] for v in m["variables"]]
            assert len(catalog) == 1; ids.append(set(catalog))
        assert ids[0].isdisjoint(ids[1])
        assert d.read(s.variables(variables), None)["applied_to_windows"] is True
        a = d.status(d.machine(machines[0])[1])["receipt"]
        s.mutate("update", "variables", {"initial_value": 45}, s.revision(), variables[1])
        assert d.status(d.machine(machines[0])[1])["status"] == "applied"
        assert d.status(d.machine(machines[0])[1])["receipt"] == a
        assert d.status(d.machine(machines[1])[1])["status"] == "pending"
        with pytest.raises(ManagementError): d.read(s.variables([variables[1]]), None)
        with pytest.raises(ManagementError): d.start(machines[0], 0)
    finally: s.close()


def test_invalid_identity_failure_and_restart_preserve_last_receipt(tmp_path):
    s,d,remote,machines,variables = setup(tmp_path)
    try:
        assert apply(s,d,machines[0])["status"] == "applied"
        previous = copy.deepcopy(remote.applied[machines[0]])
        remote.invalid_identity = True
        assert apply(s,d,machines[0])["status"] == "failed"
        assert remote.applied[machines[0]] == previous
        remote.invalid_identity = False; remote.fail_apply = True
        assert apply(s,d,machines[0])["status"] == "failed"
        assert remote.applied[machines[0]] == previous
        persisted = Deployments(s, remote)
        assert persisted.status(persisted.machine(machines[0])[1])["status"] == "failed"
        assert persisted.status(persisted.machine(machines[0])[1])["receipt"] == previous[1]["receipt"]
        with pytest.raises(ManagementError): s.mutate("update", "variables", {"initial_value": True}, s.revision(), variables[0])
    finally: s.close()


def test_missing_ack_never_becomes_applied(tmp_path):
    s,d,remote,machines,variables = setup(tmp_path)
    original = remote.apply
    def invalid(target, envelope):
        result = original(target, envelope); result["receipt"]["project_sha256"] = "wrong"; return result
    remote.apply = invalid
    try: assert apply(s,d,machines[0])["status"] == "failed"
    finally: s.close()


def test_ssh_verifies_host_and_exposes_only_fixed_operations(tmp_path, monkeypatch):
    key=tmp_path/'key';key.write_text('test fixture, not a private key');key.chmod(0o600)
    known=tmp_path/'known';known.write_text('host public fixture')
    target={"host":"host.invalid","username":"lab","expected_hostname":"LAB","identity_file":str(key),"known_hosts_file":str(known),"agent_dir":r'D:\OPCB\agent'}
    calls=[]
    def run(args, **kwargs): calls.append(args); return subprocess.CompletedProcess(args,0,b'{"ok":true}',b'')
    monkeypatch.setattr(subprocess,'run',run)
    channel=SshLabChannel(tmp_path); channel.identify(target)
    assert 'StrictHostKeyChecking=yes' in calls[0] and 'UpdateHostKeys=no' in calls[0]
    with pytest.raises(AgentError): channel.invoke(target,'exec')
    with pytest.raises(AgentError): channel.invoke(target,'apply','../../payload.json')
    target['agent_dir']=r'D:\OPCB\agent;bad'
    with pytest.raises(AgentError): channel.identify(target)


def test_runtime_stdin_uses_one_verified_ssh_and_keeps_payload_out_of_command(tmp_path, monkeypatch):
    key = tmp_path / "key"; key.write_text("fixture"); key.chmod(0o600)
    known = tmp_path / "known"; known.write_text("host fixture")
    target = {"host": "host.invalid", "username": "lab", "expected_hostname": "LAB",
              "identity_file": str(key), "known_hosts_file": str(known), "agent_dir": r"D:\OPCB\agent",
              "runtime_transport": "ssh_stdin_v1"}
    calls = []
    def run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, b'{"ok":true,"pid":123}', b'')
    monkeypatch.setattr(subprocess, "run", run)
    channel = SshLabChannel(tmp_path)
    channel.runtime(target, {"operation": "source-samples", "machine_id": "destination-a", "note": "Temperatura °C"})
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[0] == "ssh" and "StrictHostKeyChecking=yes" in args
    assert "Temperatura" not in " ".join(args)
    body = json.loads(kwargs["input"].decode("utf-8"))
    assert body["note"] == "Temperatura °C" and body["expected_hostname"] == "LAB"
    assert body["machine_id"] == "destination-a" and body["schema_version"] == 1
    with pytest.raises(AgentError): channel.invoke(target, "apply", request_body=b"{}")
    with pytest.raises(AgentError): channel.invoke(target, "telemetry", request_body=b"x" * 2_097_153)
    assert len(calls) == 1
    target["runtime_transport"] = "unknown"
    with pytest.raises(AgentError): channel.runtime(target, {})


def test_reachable_agent_without_runtime_is_a_distinct_wait_condition(tmp_path, monkeypatch):
    monkeypatch.setattr(SshLabChannel, "options", lambda self, target: [])
    monkeypatch.setattr(subprocess, "run", lambda args, **kw: subprocess.CompletedProcess(
        args, 1, b'{"ok":false,"error_code":"runtime_not_running"}', b''))
    with pytest.raises(RuntimeNotRunning):
        SshLabChannel(tmp_path).invoke({"agent_dir": r"D:\Lab", "username": "lab", "host": "lab.invalid"}, "telemetry")


def test_confirmation_is_required_before_showing_applied(tmp_path):
    import threading
    s, d, remote, machines, _ = setup(tmp_path)
    entered = threading.Event()
    release = threading.Event()
    original = remote.apply
    def delayed(target, envelope):
        entered.set()
        assert release.wait(5)
        return original(target, envelope)
    remote.apply = delayed
    try:
        d.start(machines[0], s.revision())
        assert entered.wait(5)
        assert d.status(d.machine(machines[0])[1])["status"] == "applying"
        assert "receipt" not in d.status(d.machine(machines[0])[1])
        release.set()
        d.jobs[machines[0]].join(5)
        assert d.status(d.machine(machines[0])[1])["status"] == "applied"
        remote.fail_apply = True
        assert apply(s, d, machines[0])["status"] == "failed"
        failure = d.status(d.machine(machines[0])[1])
        assert failure["last_error"] == "Rejected; previous configuration retained"
        assert failure["receipt"]["configuration_revision"] == s.revision()
    finally:
        release.set()
        s.close()


def test_hot_update_values_and_reject_structural_changes(tmp_path):
    s, d, remote, machines, variables = setup(tmp_path)
    try:
        res = apply(s, d, machines[0])
        assert res["status"] == "applied"
        initial_receipt = res["receipt"]

        s.mutate("update", "variables", {"initial_value": 77.5}, s.revision(), variables[0])
        res2 = apply(s, d, machines[0])
        assert res2["status"] == "applied"
        assert res2["receipt"]["configuration_revision"] == s.revision()
        assert res2["receipt"]["project_sha256"] != initial_receipt["project_sha256"]
        read_res = d.read(s.variables([variables[0]]), None)
        assert read_res["samples"][0]["value"] == 77.5

        mod_id = next(r[0] for r in s.db.execute("SELECT parent_id FROM nodes WHERE id=?", (variables[0],)))
        original_apply = remote.apply
        def rejecting_apply(target, envelope):
            p = json.loads(envelope["project_json"])
            vars_in_p = [v for c in p["connections"] for m in c["modules"] for v in m["variables"]]
            if len(vars_in_p) > 1 or vars_in_p[0]["name"] != "Value":
                raise AgentError("Alteração estrutural: reinício exigido.")
            return original_apply(target, envelope)
        remote.apply = rejecting_apply

        s.mutate("create", "variables", {"parent_id": mod_id, "name": "NewTag", "data_type": "Double", "initial_value": 1.0}, s.revision())
        res3 = apply(s, d, machines[0])
        assert res3["status"] == "failed"
        assert "Alteração estrutural: reinício exigido." in res3["last_error"]
        assert res3["receipt"]["project_sha256"] == res2["receipt"]["project_sha256"]
    finally:
        s.close()


def test_communication_loss_indeterminate_and_reconciliation(tmp_path):
    s, d, remote, machines, variables = setup(tmp_path)
    try:
        assert apply(s, d, machines[0])["status"] == "applied"

        committed_response = None
        def lost_ack_apply(target, envelope):
            nonlocal committed_response
            p = json.loads(envelope["project_json"])
            vals = [v for c in p["connections"] for m in c["modules"] for v in m["variables"]]
            committed_response = {
                "ok": True,
                "machine_id": envelope["machine_id"],
                "identity": {"hostname": target["expected_hostname"], "addresses": [target["host"]]},
                "receipt": {
                    "machine_id": envelope["machine_id"],
                    "project_sha256": envelope["project_sha256"],
                    "configuration_revision": envelope["configuration_revision"],
                    "hostname": target["expected_hostname"],
                    "address": target["host"],
                    "variable_count": len(vals)
                },
                "snapshot": {
                    "pid": 555,
                    "machine_id": envelope["machine_id"],
                    "config_sha256": envelope["project_sha256"],
                    "configuration_revision": envelope["configuration_revision"],
                    "samples": [{"item_id": v["item_id"], "data_type": v["data_type"], "value": v["initial_value"],
                                 "quality": 192, "source_timestamp_filetime": 134383700000000000} for v in vals]
                }
            }
            remote.applied[envelope["machine_id"]] = (copy.deepcopy(envelope), committed_response); remote.applied[str(envelope["machine_id"])] = (copy.deepcopy(envelope), committed_response)
            raise AgentError("Não foi possível obter confirmação do agente pelo SSH verificado.")

        s.mutate("update", "variables", {"initial_value": 88.0}, s.revision(), variables[0])

        orig_identify = remote.identify
        def failing_identify(target):
            raise AgentError("SSH connection timeout; host unreachable")

        def lost_ack_apply_with_net_drop(target, envelope):
            remote.identify = failing_identify
            return lost_ack_apply(target, envelope)

        remote.apply = lost_ack_apply_with_net_drop

        res = apply(s, d, machines[0])
        assert res["status"] == "indeterminate"
        assert "Comunicação perdida" in res["last_error"]

        with pytest.raises(ManagementError) as exc_info:
            d.read(s.variables([variables[0]]), None)
        assert exc_info.value.status == 503

        remote.identify = orig_identify
        probe_res = d.probe(machines[0])
        assert probe_res["deployment"]["status"] == "applied"
        assert probe_res["deployment"]["receipt"]["configuration_revision"] == s.revision()
        read_res = d.read(s.variables([variables[0]]), None)
        assert read_res["samples"][0]["value"] == 88.0
    finally:
        s.close()


def test_concurrency_blocks_simultaneous_applies(tmp_path):
    import threading
    s, d, remote, machines, _ = setup(tmp_path)
    entered = threading.Event()
    release = threading.Event()
    original_apply = remote.apply
    def blocking_apply(target, envelope):
        entered.set()
        assert release.wait(5)
        return original_apply(target, envelope)
    remote.apply = blocking_apply
    try:
        d.start(machines[0], s.revision())
        assert entered.wait(5)
        with pytest.raises(ManagementError) as exc_info:
            d.start(machines[0], s.revision())
        assert exc_info.value.status == 409
        assert exc_info.value.code == "deployment_busy"
        release.set()
        d.jobs[machines[0]].join(5)
        assert d.status(d.machine(machines[0])[1])["status"] == "applied"
    finally:
        release.set()
        s.close()


def test_ack_loss_without_commit_preserves_prior_and_marks_failed(tmp_path):
    """When ACK is lost before commit and destination unreachable, stays indeterminate; subsequent probe finds prior config intact and marks failed."""
    s, d, remote, machines, variables = setup(tmp_path)
    try:
        init_res = apply(s, d, machines[0])
        assert init_res["status"] == "applied"
        prior_receipt = init_res["receipt"]

        orig_identify = remote.identify
        def unreachable_identify(target):
            raise AgentError("SSH connection timeout; host unreachable")

        # Network fails immediately during apply; destination did not commit candidate
        def dropping_apply(target, envelope):
            remote.identify = unreachable_identify
            raise AgentError("Connection reset by peer during transfer")

        s.mutate("update", "variables", {"initial_value": 99.9}, s.revision(), variables[0])
        remote.apply = dropping_apply

        res = apply(s, d, machines[0])
        assert res["status"] == "indeterminate"

        # Reconciliation: destination becomes reachable and still has prior receipt and snapshot
        remote.identify = orig_identify
        probe_res = d.probe(machines[0])
        assert probe_res["deployment"]["status"] == "failed"
        assert "configuração anterior segue ativa" in probe_res["deployment"]["last_error"]
        assert probe_res["deployment"]["receipt"]["configuration_revision"] == prior_receipt["configuration_revision"]
    finally:
        s.close()


def test_reconciliation_validates_candidate_snapshot_and_rejects_corrupt_data(tmp_path):
    """Reconciliation must validate candidate snapshot, not only receipt. Corrupt snapshot stays indeterminate."""
    s, d, remote, machines, variables = setup(tmp_path)
    try:
        assert apply(s, d, machines[0])["status"] == "applied"

        def apply_with_corrupt_snapshot(target, envelope):
            p = json.loads(envelope["project_json"])
            vals = [v for c in p["connections"] for m in c["modules"] for v in m["variables"]]
            corrupted_response = {
                "ok": True,
                "machine_id": envelope["machine_id"],
                "identity": {"hostname": target["expected_hostname"], "addresses": [target["host"]]},
                "receipt": {
                    "machine_id": envelope["machine_id"],
                    "project_sha256": envelope["project_sha256"],
                    "configuration_revision": envelope["configuration_revision"],
                    "hostname": target["expected_hostname"],
                    "address": target["host"],
                    "variable_count": len(vals)
                },
                "snapshot": {
                    "pid": 555,
                    "machine_id": envelope["machine_id"],
                    "config_sha256": envelope["project_sha256"],
                    "configuration_revision": envelope["configuration_revision"],
                    # Corrupt samples: quality not Good (0x08 instead of 192)
                    "samples": [{"item_id": v["item_id"], "data_type": v["data_type"], "value": v["initial_value"],
                                 "quality": 8, "source_timestamp_filetime": 134383700000000000} for v in vals]
                }
            }
            remote.applied[envelope["machine_id"]] = (copy.deepcopy(envelope), corrupted_response)
            remote.applied[str(envelope["machine_id"])] = (copy.deepcopy(envelope), corrupted_response)
            raise AgentError("Timeout waiting for ACK")

        s.mutate("update", "variables", {"initial_value": 12.3}, s.revision(), variables[0])
        remote.apply = apply_with_corrupt_snapshot

        res = apply(s, d, machines[0])
        assert res["status"] == "indeterminate"

        # Probe attempts reconciliation: candidate receipt matches but snapshot quality is bad -> must NOT mark applied
        probe_res = d.probe(machines[0])
        assert probe_res["deployment"]["status"] == "indeterminate"
        assert "snapshot inválido" in probe_res["deployment"]["last_error"]
    finally:
        s.close()


def test_repeated_reconciliation_stays_indeterminate_until_resolved(tmp_path):
    """Repeated probes while target unreachable remain indeterminate without corrupting state."""
    s, d, remote, machines, variables = setup(tmp_path)
    try:
        assert apply(s, d, machines[0])["status"] == "applied"

        committed = None
        def apply_committed_then_disconnect(target, envelope):
            nonlocal committed
            p = json.loads(envelope["project_json"])
            vals = [v for c in p["connections"] for m in c["modules"] for v in m["variables"]]
            committed = {
                "ok": True,
                "machine_id": envelope["machine_id"],
                "identity": {"hostname": target["expected_hostname"], "addresses": [target["host"]]},
                "receipt": {
                    "machine_id": envelope["machine_id"],
                    "project_sha256": envelope["project_sha256"],
                    "configuration_revision": envelope["configuration_revision"],
                    "hostname": target["expected_hostname"],
                    "address": target["host"],
                    "variable_count": len(vals)
                },
                "snapshot": {
                    "pid": 555,
                    "machine_id": envelope["machine_id"],
                    "config_sha256": envelope["project_sha256"],
                    "configuration_revision": envelope["configuration_revision"],
                    "samples": [{"item_id": v["item_id"], "data_type": v["data_type"], "value": v["initial_value"],
                                 "quality": 192, "source_timestamp_filetime": 134383700000000000} for v in vals]
                }
            }
            raise AgentError("ACK lost in flight")

        orig_identify = remote.identify
        def unreachable_identify(target):
            raise AgentError("SSH connection refused")

        def apply_committed_and_disconnect(target, envelope):
            remote.identify = unreachable_identify
            return apply_committed_then_disconnect(target, envelope)

        s.mutate("update", "variables", {"initial_value": 45.6}, s.revision(), variables[0])
        remote.apply = apply_committed_and_disconnect

        res = apply(s, d, machines[0])
        assert res["status"] == "indeterminate"

        # Multiple probes while unreachable
        for _ in range(3):
            p = d.probe(machines[0])
            assert p["deployment"]["status"] == "indeterminate"

        # Target becomes reachable with committed configuration
        remote.applied[machines[0]] = (None, committed)
        remote.applied[str(machines[0])] = (None, committed)
        remote.identify = orig_identify

        p_final = d.probe(machines[0])
        assert p_final["deployment"]["status"] == "applied"
        assert p_final["deployment"]["receipt"]["configuration_revision"] == s.revision()
    finally:
        s.close()
