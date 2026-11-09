"""Source discovery/relay tests: no real SSH, registry, ABB or COM is used."""
import copy
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor

import pytest

from opc_da_bridge.management.sources import Sources, SourceError, SshSourceChannel
from opc_da_bridge.management.deployment import AgentError, RuntimeNotRunning, Deployments, export_project
from opc_da_bridge.management.store import Store, ManagementError


# Synthetic snapshot fixture values. Never use production or workstation identities in tests.
SNAPSHOT_HOST = "snapshot.example.invalid"
SNAPSHOT_PROGID = "Example.Cache.Server.1"
SNAPSHOT_CLSID = "{11111111-2222-3333-4444-555555555555}"
SNAPSHOT_AGENT_DIR = r"C:\Lab\Agent"
SNAPSHOT_ITEMS = {"Example.Numeric": "Double", "Example.Boolean": "Boolean"}
CLSID = "{22222222-3333-4444-5555-666666666666}"


@pytest.mark.parametrize("reply,expected", [
    ({"ok": False, "error_code": "source_error", "diagnostic_stage": "get_OPCGroups", "hresult": -2147024891},
     "etapa get_OPCGroups; HRESULT 0x80070005"),
    ({"ok": False, "error_code": "credential=secret", "diagnostic_stage": "C:\\private secret", "hresult": "secret"},
     "(source_error); etapa não informada; HRESULT 0x80004005"),
])
def test_source_channel_exposes_only_safe_failure_diagnostics(tmp_path, monkeypatch, reply, expected):
    from types import SimpleNamespace
    monkeypatch.setattr(SshSourceChannel, "options", lambda self, target: [])
    monkeypatch.setattr("opc_da_bridge.management.sources.subprocess.run", lambda *a, **kw:
                        SimpleNamespace(returncode=1, stdout=json.dumps(reply).encode()))
    channel = SshSourceChannel(tmp_path)
    with pytest.raises(SourceError) as error:
        channel.request({"agent_dir": "D:\\Lab", "username": "lab", "host": "lab.invalid"}, {"operation": "read"})
    assert expected in str(error.value)
    assert "secret" not in str(error.value)


@pytest.mark.parametrize(("transport", "expected_timeout"), [
    ("rust_da_com_fixture", 60),
    ("opc_da_automation", 20),
])
def test_source_channel_bounds_fixture_cleanup_timeout(tmp_path, monkeypatch, transport, expected_timeout):
    from types import SimpleNamespace
    calls = []
    monkeypatch.setattr(SshSourceChannel, "options", lambda self, target: [])

    def run(*args, **kwargs):
        calls.append(kwargs)
        reply = {"ok": False, "error_code": "source_fixture_cleanup_failed", "hresult": -2146233087}
        return SimpleNamespace(returncode=1, stdout=json.dumps(reply).encode())

    monkeypatch.setattr("opc_da_bridge.management.sources.subprocess.run", run)
    target = {"agent_dir": "D:\\Lab", "username": "lab", "host": "lab.invalid",
              "read_transport": transport}
    with pytest.raises(SourceError):
        SshSourceChannel(tmp_path).request(target, {"operation": "read"})
    assert calls[0]["timeout"] == expected_timeout


@pytest.mark.parametrize(("transport", "expected_timeout"), [
    ("rust_da_com_fixture", 60),
    ("opc_da_automation", 20),
])
def test_source_channel_reports_configured_timeout(tmp_path, monkeypatch, transport, expected_timeout):
    monkeypatch.setattr(SshSourceChannel, "options", lambda self, target: [])

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr("opc_da_bridge.management.sources.subprocess.run", timeout)
    target = {"agent_dir": "D:\\Lab", "username": "lab", "host": "lab.invalid",
              "read_transport": transport}
    with pytest.raises(AgentError, match=r"limite de consulta de %s segundos" % expected_timeout):
        SshSourceChannel(tmp_path).request(target, {"operation": "read"})


@pytest.mark.parametrize(("echoed", "accepted"), [("opc_da_automation", True), ("wrong_transport", False), (None, False)])
def test_source_channel_requires_read_transport_echo(tmp_path, monkeypatch, echoed, accepted):
    from types import SimpleNamespace
    monkeypatch.setattr(SshSourceChannel, "options", lambda self, target: [])
    reply = {"ok": True, "identity": {}, "samples": []}
    if echoed is not None:
        reply["transport"] = echoed
    monkeypatch.setattr("opc_da_bridge.management.sources.subprocess.run", lambda *a, **kw:
                        SimpleNamespace(returncode=0, stdout=json.dumps(reply).encode()))
    target = {"agent_dir": "D:\\Lab", "username": "lab", "host": "lab.invalid",
              "read_transport": "opc_da_automation"}
    try:
        SshSourceChannel(tmp_path).request(target, {"operation": "read"})
        result = True
    except SourceError:
        result = False
    assert result is accepted


class SourceChannel:
    def __init__(self):
        self.count = 1; self.fail = False; self.wrong = False; self.requests = []; self.transport = "opc_da_automation"

    def request(self, target, request):
        self.requests.append(copy.deepcopy(request))
        if self.fail: raise AgentError("offline")
        identity = {"hostname": "OTHER" if self.wrong else target["expected_hostname"], "addresses": [target["host"]], "registry_view": "32-bit", "process_architecture": 32}
        if request["operation"] == "discover":
            return {"ok": True, "identity": identity, "servers": [{"prog_id": "Lab.Server.%d" % n, "clsid": CLSID, "registry_view": "32-bit"} for n in range(self.count)]}
        def sample(v):
            source_type = v.get("source_data_type", v["data_type"])
            converting = v.get("source_conversion") == "float_to_double"
            return {**v, "value": (61.75 if converting else False if v["data_type"] == "Boolean" else 7.0),
                    "quality": 192, "source_timestamp_filetime": 116444736000000000 if self.transport == "rust_lab_loopback_snapshot" else 1234567,
                    "hresult": 0, "source_vartype": 4 if source_type == "Float" else 11 if source_type == "Boolean" else 5,
                    "published_vartype": 11 if v["data_type"] == "Boolean" else 5,
                    "read_method": "OPCGroup.SyncRead" if self.transport == "opc_da_automation" else None}
        return {"ok": True, "identity": identity, "transport": self.transport,
                "read_method": "GET /v1/snapshot over SSH-local loopback" if self.transport == "rust_lab_loopback_snapshot" else None, "source_pid": 42 if self.transport == "rust_lab_loopback_snapshot" else None,
                "source_session": 1 if self.transport == "rust_lab_loopback_snapshot" else None,
                "samples": [sample(v) for v in request["items"]]}


class Destination:
    def __init__(self): self.sent = []; self.fail = False
    def runtime(self, target, payload):
        if self.fail: raise AgentError("lost ack")
        self.sent.append(copy.deepcopy(payload))
        return {**{k: payload[k] for k in ("machine_id", "configuration_revision", "config_sha256", "source_module_id")},
                "ok": True, "pid": 123, "session_id": 9, "sequence": payload.get("sequence"), "all_good": all(s["hresult"] >= 0 and s["quality"] & 192 == 192 for s in payload.get("samples", []))}


@pytest.fixture
def lab(tmp_path):
    store = Store(tmp_path); dest = Destination(); deployments = Deployments(store, dest)
    channel = SourceChannel(); sources = Sources(store, deployments, channel)
    target = {"host": "origin.invalid", "expected_hostname": "ORIGIN", "enabled": True,
              "read_transport": channel.transport, "source_environment": "laboratory"}
    (tmp_path / "source-agents.json").write_text(json.dumps({"sources": {"origin.invalid": target}}))
    yield store, deployments, sources, channel, dest
    store.close()


def configure_persistent_snapshot(lab, include_second_machine=False):
    store, deployments, sources, channel, _ = lab
    host = SNAPSHOT_HOST
    channel.transport = "rust_lab_loopback_snapshot"
    source_items = list(SNAPSHOT_ITEMS)
    target = {"host": host, "expected_hostname": "snapshot-lab", "expected_address": host,
              "expected_process_architecture": 32, "enabled": True,
              "agent_dir": SNAPSHOT_AGENT_DIR,
              "read_transport": "rust_lab_loopback_snapshot",
              "lab_snapshot": {"enabled": True, "prog_id": SNAPSHOT_PROGID,
                  "clsid": SNAPSHOT_CLSID, "port": 49321,
                  "items": [{"source_item_id": item, "data_type": SNAPSHOT_ITEMS[item]} for item in source_items]}}
    (store.directory / "source-agents.json").write_text(json.dumps({"sources": {host: target}}))

    def add(kind, data):
        before = {row[0] for row in store.db.execute("SELECT id FROM nodes")}
        store.mutate("create", kind, data, store.revision())
        return next(row[0] for row in store.db.execute("SELECT id FROM nodes") if row[0] not in before)

    def machine_tree(name, machine_host):
        connection = add("connections", {"name": name + " connection"})
        machine = add("machines", {"parent_id": connection, "name": name, "host": machine_host})
        module = add("modules", {"parent_id": machine, "name": "TESTE", "source_kind": "simulated"})
        folder = add("folders", {"parent_id": module, "name": "TESTE1"})
        number = add("variables", {"parent_id": folder, "name": "TESTE2", "item_id": source_items[0],
                    "source_id": source_items[0], "data_type": "Double", "initial_value": 28})
        boolean = add("variables", {"parent_id": folder, "name": "DIGITAL", "item_id": source_items[1],
                    "source_id": source_items[1], "data_type": "Boolean", "initial_value": True})
        return machine, number, boolean

    machine_id, number_id, boolean_id = machine_tree("LabMachine", host)
    second = machine_tree("Second target", "other.example.invalid") if include_second_machine else None
    profile = {"schema_version": 1, "enabled": True, "profile_id": "snapshot-cache-view",
               "machine_id": machine_id, "source_host": host, "prog_id": SNAPSHOT_PROGID,
               "clsid": SNAPSHOT_CLSID, "interval_s": 5,
               "bindings": [
                   {"variable_id": number_id, "published_item_id": source_items[0],
                    "source_item_id": source_items[0], "data_type": "Double"},
                   {"variable_id": boolean_id, "published_item_id": source_items[1],
                    "source_item_id": source_items[1], "data_type": "Boolean"}]}
    (store.directory / "lab-dashboard-snapshot.json").write_text(json.dumps(profile))
    return machine_id, number_id, boolean_id, profile, second


@pytest.mark.parametrize("count", [0, 1, 3])
def test_discovery_none_single_multiple_does_not_connect(lab, count):
    store, deploy, sources, channel, dest = lab
    channel.count = count
    result = sources.discover("origin.invalid")
    assert len(result["servers"]) == count and result["availability_tested"] is False
    assert result["identity"]["hostname"] == "ORIGIN"
    assert channel.requests == [{"operation": "discover"}]
    assert not dest.sent
    assert Sources(store, deploy, channel).catalogs[result["discovery_id"]] == result


def test_same_windows_host_can_keep_snapshot_and_opc_da_profiles_isolated(lab):
    store, _, sources, _, _ = lab
    host = SNAPSHOT_HOST
    snapshot = {"host": host, "enabled": True,
                "read_transport": "rust_lab_loopback_snapshot", "profile_id": "cache-only"}
    automation = {"host": host, "enabled": True, "read_transport": "opc_da_automation",
                  "allowed_progids": ["Example.Source.1"],
                  "source_environment": "laboratory"}
    (store.directory / "source-agents.json").write_text(json.dumps({"schema_version": 1,
        "sources": {host: snapshot, "second-source-profile": automation}}))

    # Read the persisted mapping back: selecting one transport must not replace
    # or accidentally return the other profile for the same SSH identity.
    assert sources.provision(host, "rust_lab_loopback_snapshot")["profile_id"] == "cache-only"
    selected = sources.provision(host, "opc_da_automation", "Example.Source.1")
    assert selected["source_environment"] == "laboratory"
    with pytest.raises(AgentError):
        sources.provision(host, "opc_da_automation", "Example.Cache.1")


def test_discovery_and_selection_can_use_hash_pinned_direct_com_fixture(lab):
    store, _, sources, channel, _ = lab
    channel.transport = "rust_da_com_fixture"
    channel.count = 1
    host = "origin.invalid"
    profiles = {
        host: {"host": host, "expected_hostname": "ORIGIN", "enabled": True,
               "read_transport": "rust_lab_loopback_snapshot",
               "allowed_progids": ["Example.Cache.1"]},
        "origin-direct-fixture": {"host": host, "expected_hostname": "ORIGIN", "enabled": True,
               "read_transport": "rust_da_com_fixture", "source_environment": "laboratory",
               "allowed_progids": ["Lab.Server.0"]},
    }
    (store.directory / "source-agents.json").write_text(json.dumps({"schema_version": 1, "sources": profiles}))
    result = sources.discover(host)
    assert result["transport"] == "rust_da_com_fixture"
    selection = {"source_kind": "opc_da", "source_host": host,
                 "source_progid": "Lab.Server.0", "source_clsid": CLSID,
                 "source_discovery_id": result["discovery_id"]}
    # The fake catalog is independent from provisioning; use its listed server ID.
    selection["source_progid"] = result["servers"][0]["prog_id"]
    selection["source_clsid"] = result["servers"][0]["clsid"]
    sources.validate_selection(selection)
    assert sources.provision(host, prog_id="Lab.Server.0")["read_transport"] == "rust_da_com_fixture"
    assert sources.provision(host, prog_id="Example.Cache.1")["read_transport"] == "rust_lab_loopback_snapshot"


def test_relay_uses_direct_com_fixture_transport_and_preserves_samples(lab):
    store, deployments, sources, channel, dest = lab
    channel.transport = "rust_da_com_fixture"
    (store.directory / "source-agents.json").write_text(json.dumps({"schema_version": 1, "sources": {
        "origin.invalid": {"host": "origin.invalid", "expected_hostname": "ORIGIN", "enabled": True,
                           "read_transport": "rust_da_com_fixture", "allowed_progids": ["Lab.Server.0"],
                           "source_environment": "laboratory"}}}))
    mid, md, variable = configure(lab, "direct-com")
    # configure() creates and selects a separate discovery record using the same fake catalog.
    state, machine = deployments.machine(mid)
    module = next(m for m in machine["modules"] if m["id"] == md)
    sources.poll_once(machine, module, state)
    batch = next(item for item in dest.sent if item.get("operation") == "source-samples")
    sample = batch["samples"][0]
    assert sources.status(mid)["origins"][0]["transport"] == "rust_da_com_fixture"
    assert sample["value"] == 7.0 and sample["quality"] == 192
    assert sample["source_timestamp_filetime"] == 1234567 and sample["hresult"] == 0


@pytest.mark.parametrize("mode", ["unprovisioned", "offline", "identity"])
def test_discovery_failures_never_misattribute_a_list(lab, mode):
    _, _, sources, channel, _ = lab
    channel.fail = mode == "offline"; channel.wrong = mode == "identity"
    with pytest.raises(ManagementError): sources.discover("other.invalid" if mode == "unprovisioned" else "origin.invalid")
    assert not sources.catalogs
    if mode == "unprovisioned": assert not channel.requests


def configure(lab, suffix="a"):
    store, deployments, sources, channel, dest = lab
    def add(kind, data):
        old = {r[0] for r in store.db.execute("SELECT id FROM nodes")}
        store.mutate("create", kind, data, store.revision())
        return next(r[0] for r in store.db.execute("SELECT id FROM nodes") if r[0] not in old)
    discovery = sources.discover("origin.invalid")
    c = add("connections", {"name": "Connection" + suffix})
    mid = add("machines", {"parent_id": c, "name": "Destination" + suffix, "host": "destination" + suffix + ".invalid"})
    module = {"name": "Source" + suffix, "source_kind": "opc_da", "source_host": "origin.invalid", "source_progid": "Lab.Server.0", "source_clsid": CLSID,
              "source_discovery_id": discovery["discovery_id"], "update_interval_s": 10}
    sources.validate_selection(module)
    md = add("modules", dict(module, parent_id=mid))
    folder = add("folders", {"parent_id": md, "name": "Folder"})
    variable = add("variables", {"parent_id": folder, "name": "Value", "source_id": "Source.Exact:Value", "item_id": "Published." + suffix,
                                 "data_type": "Double", "initial_value": 999})
    (store.directory / "agents.json").write_text(json.dumps({"schema_version": 1, "targets": {
        m["id"]: {"host": m["host"], "expected_hostname": "DEST"} for c in store.state(False)["connections"] for m in c["machines"]}}))
    state, machine = deployments.machine(mid)
    envelope = export_project(state, mid, deployments.target(machine))
    from opc_da_bridge.management.deployment import fingerprint
    deployments.records[mid] = {"status": "applied", "fingerprint": fingerprint(machine), "receipt": {
        "configuration_revision": state["revision"], "project_sha256": envelope["project_sha256"]}}
    return mid, md, variable


def test_selection_must_belong_to_discovered_host_and_server(lab):
    _, _, sources, _, _ = lab
    result = sources.discover("origin.invalid")
    selection = dict(source_kind="opc_da", source_host="origin.invalid", source_progid="Lab.Server.0", source_clsid=CLSID, source_discovery_id=result["discovery_id"])
    sources.validate_selection(selection)
    for field, value in [("source_host", "other.invalid"), ("source_progid", "Other.1"), ("source_clsid", "{ffffffff-2222-3333-4444-555555555555}"), ("source_discovery_id", "forged")]:
        with pytest.raises(ManagementError): sources.validate_selection(dict(selection, **{field: value}))


def test_relay_preserves_source_and_published_ids_vqt_hresult_and_isolation(lab):
    store, deployments, sources, channel, dest = lab
    targets = [configure(lab, suffix) for suffix in ("a", "b")]
    for mid, md, _ in targets:
        state, machine = deployments.machine(mid); module = next(m for m in machine["modules"] if m["id"] == md)
        sources.poll_once(machine, module, state)
    batches = [s for s in dest.sent if s["operation"] == "source-samples"]
    assert len(batches) == 2
    assert {b["machine_id"] for b in batches} == {v[0] for v in targets}
    assert {b["samples"][0]["item_id"] for b in batches} == {"Published.a", "Published.b"}
    for batch in batches:
        s = batch["samples"][0]
        assert s["source_item_id"] == "Source.Exact:Value" and s["value"] == 7.0
        assert s["quality"] == 192 and s["source_timestamp_filetime"] == 1234567 and s["hresult"] == 0
        assert s["value"] != 999  # Never publish configured simulation as source data.
    assert all(origin["source_environment"] == "laboratory"
               for mid, _, _ in targets for origin in sources.status(mid)["origins"])
    assert all(origin["read_method"] == "OPCGroup.SyncRead"
               for mid, _, _ in targets for origin in sources.status(mid)["origins"])


def test_explicit_float_to_double_conversion_preserves_source_evidence(lab):
    _, _, sources, _, _ = lab
    variable = {"item_id": "Lab.Numeric",
                "source_id": "ExampleChannel.Device.Ramp",
                "data_type": "Double", "source_data_type": "Float",
                "source_conversion": "float_to_double"}
    request = sources._source_item_request(variable)
    assert request == {"item_id": variable["item_id"],
                       "source_item_id": variable["source_id"], "data_type": "Double",
                       "source_data_type": "Float", "source_conversion": "float_to_double"}
    sample = {**request, "value": 61.75, "quality": 192,
              "source_timestamp_filetime": 134384724655270000, "hresult": 0,
              "source_vartype": 4, "published_vartype": 5}
    checked = sources._validate_opc_samples([variable], [sample], "opc_da_automation")
    assert checked[0]["value"] == 61.75
    assert checked[0]["source_vartype"] == 4
    assert checked[0]["published_vartype"] == 5
    assert checked[0]["quality"] == 192
    assert checked[0]["source_timestamp_filetime"] == 134384724655270000

    for key, value in (("source_vartype", 5), ("published_vartype", 4),
                       ("source_conversion", "float_to_double_typo")):
        invalid = dict(sample, **{key: value})
        with pytest.raises(SourceError):
            sources._validate_opc_samples([variable], [invalid], "opc_da_automation")


@pytest.mark.parametrize('kind,value,source_vt,published_vt', [
    ('Double', 12.5, 4, 5), ('Double', 7, 3, 5),
    ('Integer', -2147483648, 2, 3), ('Integer', 42, 8, 3),
    ('String', 'Ação\x00🚀', 8, 8), ('String', '12.5', 5, 8),
    ('Boolean', False, 11, 11), ('Boolean', True, 3, 11),
])
def test_auto_scalar_output_keeps_source_evidence_and_timestamp(lab, kind, value, source_vt, published_vt):
    _, _, sources, _, _ = lab
    variable = {'item_id': 'Published.Selected', 'source_id': 'Authorized.Source',
                'data_type': kind, 'source_data_type': 'Auto', 'source_conversion': 'automatic'}
    raw = dict(sources._source_item_request(variable), value=value, source_vartype=source_vt,
               published_vartype=published_vt, quality=192, hresult=0,
               source_timestamp_filetime=134384724655270000)
    result = sources._validate_opc_samples([variable], [raw], 'opc_da_automation')[0]
    assert result['value'] == value
    assert result['source_vartype'] == source_vt and result['published_vartype'] == published_vt
    assert result['source_data_type'] == 'Auto' and result['source_conversion'] == 'automatic'
    assert result['quality'] == 192 and result['source_timestamp_filetime'] == 134384724655270000


@pytest.mark.parametrize('kind,value,source_vt,published_vt', [
    ('Double', float('inf'), 4, 5), ('Double', True, 11, 5),
    ('Integer', 2.5, 5, 3), ('Integer', 2147483648, 19, 3),
    ('String', 'x'*4097, 8, 8), ('String', '\ud800', 8, 8),
    ('Boolean', 1, 3, 11), ('Boolean', False, 8203, 11),
    ('Boolean', False, True, 11), ('String', 'x', 8, 3),
])
def test_auto_scalar_rejects_invalid_output_or_variant(lab, kind, value, source_vt, published_vt):
    _, _, sources, _, _ = lab
    variable = {'item_id': 'Published.Selected', 'source_id': 'Authorized.Source',
                'data_type': kind, 'source_data_type': 'Auto', 'source_conversion': 'automatic'}
    raw = dict(sources._source_item_request(variable), value=value, source_vartype=source_vt,
               published_vartype=published_vt, quality=192, hresult=0,
               source_timestamp_filetime=134384724655270000)
    with pytest.raises(SourceError):
        sources._validate_opc_samples([variable], [raw], 'opc_da_automation')


@pytest.mark.parametrize('kind,initial', [('Double', 0.0), ('Integer', 0), ('String', ''), ('Boolean', False)])
def test_store_persists_automatic_four_type_binding_without_renaming(lab, kind, initial):
    store, _, _, _, _ = lab
    _, _, variable_id = configure(lab, 'automatic')
    old = store.variables([variable_id])[0]
    store.mutate('update', 'variables', {'data_type': kind, 'initial_value': initial,
                 'source_data_type': 'Auto', 'source_conversion': 'automatic'}, store.revision(), variable_id)
    new = store.variables([variable_id])[0]
    assert new['item_id'] == old['item_id'] and new['source_id'] == old['source_id']
    assert new['data_type'] == kind and new['source_conversion'] == 'automatic'


def test_store_persists_only_authorized_opc_float_to_double_mapping(lab):
    store, _, _, _, _ = lab
    _, _, variable_id = configure(lab, "float-conversion")
    variable = store.variables([variable_id])[0]
    revision = store.revision()
    update = {key: value for key, value in variable.items()
              if key not in ("id", "read_only")}
    update.update(source_data_type="Float", source_conversion="float_to_double")
    store.mutate("update", "variables", update, revision, variable["id"])
    persisted = store.variables([variable["id"]])[0]
    assert persisted["item_id"] == variable["item_id"]
    assert persisted["source_id"] == "Source.Exact:Value"
    assert persisted["data_type"] == "Double"
    assert persisted["source_data_type"] == "Float"
    assert persisted["source_conversion"] == "float_to_double"

    revision = store.revision()
    invalid = dict(update, source_conversion="float_to_integer")
    with pytest.raises(ManagementError):
        store.mutate("update", "variables", invalid, revision, variable["id"])
    assert store.revision() == revision
    assert store.variables([variable["id"]])[0] == persisted


def test_float_to_double_is_relayed_to_published_item_without_losing_origin_metadata(lab):
    store, deployments, sources, channel, dest = lab
    machine_id, module_id, variable_id = configure(lab, "float-relay")
    variable = store.variables([variable_id])[0]
    data = {key: value for key, value in variable.items() if key not in ("id", "read_only")}
    data.update(source_id="ExampleChannel.Device.Ramp",
                source_data_type="Float", source_conversion="float_to_double")
    store.mutate("update", "variables", data, store.revision(), variable_id)
    state, machine = deployments.machine(machine_id)
    module = next(m for m in machine["modules"] if m["id"] == module_id)
    envelope = export_project(state, machine_id, deployments.target(machine))
    from opc_da_bridge.management.deployment import fingerprint
    deployments.records[machine_id] = {"status": "applied", "fingerprint": fingerprint(machine), "receipt": {
        "configuration_revision": state["revision"], "project_sha256": envelope["project_sha256"]}}

    sources.poll_once(machine, module, state)
    assert channel.requests[-1]["items"] == [{
        "source_item_id": "ExampleChannel.Device.Ramp",
        "item_id": "Published.float-relay", "data_type": "Double",
        "source_data_type": "Float", "source_conversion": "float_to_double"}]
    sent = next(x for x in dest.sent if x.get("operation") == "source-samples")
    sample = sent["samples"][0]
    assert sample["item_id"] == "Published.float-relay"
    assert sample["source_item_id"] == "ExampleChannel.Device.Ramp"
    assert sample["value"] == 61.75 and sample["data_type"] == "Double"
    assert sample["source_vartype"] == 4 and sample["published_vartype"] == 5
    assert sample["source_data_type"] == "Float" and sample["source_conversion"] == "float_to_double"
    assert sample["quality"] == 192 and sample["source_timestamp_filetime"] == 1234567
    exported = json.loads(envelope["project_json"])
    configured = exported["connections"][0]["modules"][0]["variables"][0]
    assert configured["item_id"] == "Published.float-relay"
    assert configured["source"]["item_id"] == "ExampleChannel.Device.Ramp"
    assert configured["source"]["source_data_type"] == "Float"


def test_same_type_boolean_metadata_matches_implicit_destination_binding(lab):
    store, deployments, sources, channel, dest = lab
    machine_id, module_id, variable_id = configure(lab, "boolean-source")
    variable = store.variables([variable_id])[0]
    update = {key: value for key, value in variable.items() if key not in ("id", "read_only")}
    update.update(data_type="Boolean", initial_value=False, source_id="Source.Exact:Digital")
    store.mutate("update", "variables", update, store.revision(), variable_id)

    state, machine = deployments.machine(machine_id)
    module = next(m for m in machine["modules"] if m["id"] == module_id)
    envelope = export_project(state, machine_id, deployments.target(machine))
    configured = json.loads(envelope["project_json"])["connections"][0]["modules"][0]["variables"][0]
    assert configured["data_type"] == "Boolean"
    assert "source_data_type" not in configured["source"]

    sources.poll_once(machine, module, state)
    sent = next(x for x in dest.sent if x.get("operation") == "source-samples")
    sample = sent["samples"][0]
    assert sample["source_item_id"] == "Source.Exact:Digital"
    assert sample["data_type"] == "Boolean" and sample["value"] is False
    assert sample["source_vartype"] == 11 and sample["published_vartype"] == 11
    assert sample["source_data_type"] is None and sample["source_conversion"] is None
    assert sample["quality"] == 192 and sample["hresult"] == 0

    from opc_da_bridge.management.deployment import fingerprint
    deployments.records[machine_id] = {"status": "applied", "fingerprint": fingerprint(machine), "receipt": {
        "configuration_revision": state["revision"], "project_sha256": envelope["project_sha256"]}}
    dest.snapshot = lambda target: {"ok": True, "machine_id": machine_id,
        "identity": {"hostname": target["expected_hostname"], "addresses": [target["host"]]},
        "snapshot": {"config_sha256": envelope["project_sha256"], "machine_id": machine_id, "pid": 321,
            "samples": [{"item_id": "Published.boolean-source", "data_type": "Boolean", "value": False,
                "quality": 64, "source_timestamp_filetime": None, "hresult": 0,
                "source_module_id": module_id, "source_vartype": 11, "published_vartype": 11,
                "source_data_type": None, "source_conversion": None}]}}
    dashboard_sample = deployments.read([store.variables([variable_id])[0]], None)["samples"][0]
    assert dashboard_sample["source_kind"] == "opc_da"
    assert dashboard_sample["source_data_type"] == "Boolean"
    assert dashboard_sample["quality"] == "Uncertain" and dashboard_sample["source_timestamp"] is None


def test_snapshot_transport_is_not_accepted_as_an_opc_da_source(lab):
    store, deployments, sources, channel, dest = lab
    mid, md, _ = configure(lab, "snapshot-source-rejected")
    provision = json.loads((store.directory / "source-agents.json").read_text())
    provision["sources"]["origin.invalid"]["read_transport"] = channel.transport
    (store.directory / "source-agents.json").write_text(json.dumps(provision))
    channel.transport = "rust_lab_loopback_snapshot"
    state, machine = deployments.machine(mid)
    module = next(m for m in machine["modules"] if m["id"] == md)
    sources.poll_once(machine, module, state)
    assert sources.status(mid)["status"] == "unavailable"
    samples = [payload for payload in dest.sent if payload.get("operation") == "source-samples"]
    assert len(samples) == 1 and samples[0]["samples"][0]["quality"] == 8
    assert samples[0]["samples"][0]["value"] is None


def test_persistent_dashboard_snapshot_is_overlay_only_and_isolated_between_machines(lab):
    store, deployments, sources, channel, _ = lab
    machine_id, number_id, boolean_id, profile, second = configure_persistent_snapshot(lab, True)
    before = store.state(False)
    _, machine = deployments.machine(machine_id)
    exported_before = export_project(before, machine_id, {"expected_hostname": "snapshot-lab", "host": SNAPSHOT_HOST})
    sources._poll_dashboard_snapshot()
    after = store.state(False)
    exported_after = export_project(after, machine_id, {"expected_hostname": "snapshot-lab", "host": SNAPSHOT_HOST})
    rows = store.variables([number_id, boolean_id, second[1], second[2]])
    samples = sources.dashboard_samples_for(rows)

    assert after["revision"] == before["revision"]
    assert [v["item_id"] for v in rows] == [
        profile["bindings"][0]["published_item_id"], profile["bindings"][1]["published_item_id"],
        profile["bindings"][0]["published_item_id"], profile["bindings"][1]["published_item_id"]]
    assert exported_after["project_json"] == exported_before["project_json"]
    assert '"source"' not in exported_after["project_json"]
    assert channel.requests[-1]["items"] == [
        {"source_item_id": profile["bindings"][0]["source_item_id"], "item_id": profile["bindings"][0]["published_item_id"], "data_type": "Double"},
        {"source_item_id": profile["bindings"][1]["source_item_id"], "item_id": profile["bindings"][1]["published_item_id"], "data_type": "Boolean"}]
    assert set(samples) == {number_id, boolean_id}
    assert samples[number_id]["value"] == 7.0 and samples[number_id]["quality"] == "Good"
    assert samples[number_id]["data_type"] == "Double" and samples[number_id]["source_vartype"] == 5
    assert samples[boolean_id]["value"] is False and samples[boolean_id]["quality"] == "Good"
    assert samples[boolean_id]["data_type"] == "Boolean" and samples[boolean_id]["source_vartype"] == 11
    assert all(samples[x]["source_timestamp"].endswith("Z") for x in (number_id, boolean_id))
    assert all(samples[x]["lab_snapshot"] and "hresult" not in samples[x] for x in (number_id, boolean_id))
    reloaded = Sources(store, deployments, channel)
    after_restart = reloaded.dashboard_samples_for(rows)
    assert reloaded.dashboard_status()["status"] == "unavailable"
    assert all(after_restart[x]["value"] is None and after_restart[x]["quality"] == "Bad"
               for x in (number_id, boolean_id))


def test_persistent_snapshot_outage_clears_current_values_and_recovers(lab):
    store, _, sources, channel, _ = lab
    _, number_id, boolean_id, _, _ = configure_persistent_snapshot(lab)
    sources._poll_dashboard_snapshot()
    good = sources.dashboard_samples_for(store.variables([number_id, boolean_id]))
    assert good[number_id]["value"] == 7.0 and good[boolean_id]["value"] is False

    channel.fail = True
    sources._poll_dashboard_snapshot()
    bad = sources.dashboard_samples_for(store.variables([number_id, boolean_id]))
    assert sources.dashboard_status()["status"] == "unavailable"
    assert all(s["value"] is None and s["quality"] == "Bad" and s["quality_code"] == 8 for s in bad.values())
    assert all(s["source_timestamp"] is None and "hresult" not in s for s in bad.values())

    channel.fail = False
    sources._poll_dashboard_snapshot()
    recovered = sources.dashboard_samples_for(store.variables([number_id, boolean_id]))
    assert sources.dashboard_status()["status"] == "connected"
    assert recovered[number_id]["value"] == 7.0 and recovered[number_id]["quality"] == "Good"
    assert recovered[boolean_id]["value"] is False and recovered[boolean_id]["quality"] == "Good"


def test_persistent_snapshot_expiry_does_not_serve_old_good_as_current(lab):
    store, _, sources, _, _ = lab
    _, number_id, boolean_id, _, _ = configure_persistent_snapshot(lab)
    sources._poll_dashboard_snapshot()
    sources.dashboard_state["checked_at"] = "2000-01-01T00:00:00Z"
    samples = sources.dashboard_samples_for(store.variables([number_id, boolean_id]))
    assert sources.dashboard_status()["status"] == "unavailable"
    assert all(s["value"] is None and s["quality"] == "Bad" and s["quality_code"] == 8 for s in samples.values())
    assert all(s["source_timestamp"] is None for s in samples.values())


def test_opc_da_variables_bypass_legacy_snapshot_overlay(lab):
    store, _, sources, channel, _ = lab
    _, number_id, boolean_id, _, _ = configure_persistent_snapshot(lab)
    sources._poll_dashboard_snapshot()
    assert sources.dashboard_samples_for(store.variables([number_id, boolean_id]))

    state = store.state(False)
    module = next(module for connection in state["connections"] for machine in connection["machines"]
                  for module in machine["modules"] if module["name"] == "TESTE")
    source = {"source_kind": "opc_da", "source_host": SNAPSHOT_HOST,
              "source_progid": "Example.Source.1", "source_clsid": CLSID,
              "source_discovery_id": "00000000-0000-0000-0000-000000000001", "update_interval_s": 5}
    store.mutate("update", "modules", source, store.revision(), module["id"])

    # The old sidecar still binds these variable UUIDs and published Item IDs;
    # it must not shadow a sample once those variables belong to a DA relay.
    assert sources.dashboard_samples_for(store.variables([number_id, boolean_id])) == {}
    calls = len(channel.requests)
    sources._poll_dashboard_snapshot()
    assert len(channel.requests) == calls
    assert sources.dashboard_status()["status"] == "superseded"
    assert len(sources.dashboard_status()["bindings"]) == 2
    (store.directory / "lab-dashboard-snapshot.json").write_text("invalid", encoding="utf-8")
    assert sources._invalid_profile_samples(store.variables([number_id, boolean_id])) == {}


def test_invalid_persistent_profile_downgrades_last_snapshot_without_changing_catalog(lab):
    store, _, sources, channel, _ = lab
    _, number_id, boolean_id, profile, _ = configure_persistent_snapshot(lab)
    sources._poll_dashboard_snapshot()
    revision = store.revision()
    profile["source_host"] = "other.example.invalid"
    (store.directory / "lab-dashboard-snapshot.json").write_text(json.dumps(profile))
    sources._poll_dashboard_snapshot()
    current = sources.dashboard_samples_for(store.variables([number_id, boolean_id]))
    assert sources.dashboard_status()["status"] == "unavailable"
    assert store.revision() == revision
    assert all(sample["value"] is None and sample["quality"] == "Bad" for sample in current.values())
    assert len(channel.requests) == 1  # Invalid host was rejected before any remote request.
    # First-start path: no valid in-memory/cache profile exists, but a malformed
    # sidecar must still not fall through to the Windows cache for authorized IDs.
    sources.dashboard_cache_path.unlink()
    first_start = Sources(store, sources.deployments, channel)
    first_start.dashboard_profile = None
    no_fallback = first_start.dashboard_samples_for(store.variables([number_id, boolean_id]))
    assert set(no_fallback) == {number_id, boolean_id}
    assert all(sample["value"] is None and sample["quality"] == "Bad" for sample in no_fallback.values())


@pytest.mark.parametrize("failure", ["offline", "identity"])
def test_read_failures_send_bad_without_inventing_value_or_timestamp_and_recover(lab, failure):
    store, deployments, sources, channel, dest = lab
    mid, md, _ = configure(lab); state, machine = deployments.machine(mid); module = machine["modules"][0]
    sources.poll_once(machine, module, state)
    channel.fail = failure == "offline"; channel.wrong = failure == "identity"
    sources.poll_once(machine, module, state)
    bad = dest.sent[-1]["samples"][0]
    assert bad["value"] is None and bad["source_timestamp_filetime"] is None and bad["quality"] == 8 and bad["hresult"] < 0
    channel.fail = channel.wrong = False
    sources.poll_once(machine, module, state)
    assert dest.sent[-1]["samples"][0]["value"] == 7 and sources.status(mid)["status"] == "connected"


def test_lost_destination_ack_cannot_be_reported_as_connected(lab):
    store, deployments, sources, _, dest = lab
    mid, _, _ = configure(lab); state, machine = deployments.machine(mid)
    dest.fail = True; sources.poll_once(machine, machine["modules"][0], state)
    assert sources.status(mid)["status"] == "unavailable" and not sources.sessions


def test_cycle_interval_accounts_for_io_time_and_waiting_does_not_backoff(lab, monkeypatch):
    _, _, sources, _, _ = lab
    key = ("destination-a", "origin-a")
    monkeypatch.setattr("opc_da_bridge.management.sources.time.monotonic", lambda: 103.)
    sources._schedule_poll(key, "connected", 100., 5)
    assert sources.next_poll[key] == 105.
    monkeypatch.setattr("opc_da_bridge.management.sources.time.monotonic", lambda: 107.)
    sources._schedule_poll(key, "degraded", 100., 5)
    assert sources.next_poll[key] == 107.  # no overlap; next loop can start after an overrun
    sources.failures[key] = 4
    sources._schedule_poll(key, "waiting", 100., 5)
    assert sources.next_poll[key] == 108. and sources.failures[key] == 0
    sources._schedule_poll(key, "unavailable", 100., 5)
    assert sources.next_poll[key] == 117. and sources.failures[key] == 1
    for _ in range(5): sources._schedule_poll(key, "unavailable", 100., 5)
    assert sources.next_poll[key] == 167.
    sources._schedule_poll(key, "connected", 105., 5)
    assert sources.next_poll[key] == 110. and sources.failures[key] == 0


def test_waiting_for_destination_does_not_read_source_or_claim_connection(lab):
    _, deployments, sources, channel, dest = lab
    mid, _, _ = configure(lab)
    state, machine = deployments.machine(mid)
    channel.requests.clear()
    def waiting(*args): raise RuntimeNotRunning("waiting for laboratory activation")
    dest.runtime = waiting
    sources.poll_once(machine, machine["modules"][0], state)
    assert sources.status(mid)["status"] == "waiting"
    assert not channel.requests and not sources.sessions


def test_real_source_without_destination_never_uses_linux_simulator(lab):
    store, deployments, sources, _, _ = lab
    mid, _, vid = configure(lab)
    (store.directory / "agents.json").unlink()
    with pytest.raises(ManagementError): deployments.read(store.variables([vid]), None)


def test_concurrent_discoveries_and_invalid_settings_preserve_store(lab):
    store, _, sources, _, _ = lab
    with ThreadPoolExecutor(max_workers=4) as pool:
        records = list(pool.map(lambda _: sources.discover("origin.invalid"), range(8)))
    assert len({r["discovery_id"] for r in records}) == 8
    assert len(json.loads(sources.path.read_text())) == 8
    mid, md, _ = configure(lab); before = store.state(False)
    with pytest.raises(ManagementError): store.mutate("update", "modules", {"update_interval_s": 1}, store.revision(), md)
    assert store.state(False) == before


def test_source_connection_hresult_is_preserved_per_item(lab):
    store, deployments, sources, channel, dest = lab
    mid, _, _ = configure(lab); state, machine = deployments.machine(mid)
    original = channel.request
    def denied(target, request):
        if request["operation"] == "read": raise SourceError("Access denied", -2147024891)
        return original(target, request)
    channel.request = denied
    sources.poll_once(machine, machine["modules"][0], state)
    assert dest.sent[-1]["samples"][0]["hresult"] == -2147024891
    assert sources.status(mid)["status"] == "unavailable"


def test_snapshot_failure_code_is_reported_without_secrets_and_recovers(lab):
    store, deployments, sources, channel, dest = lab
    mid, _, _ = configure(lab)
    state, machine = deployments.machine(mid)
    original = channel.request
    def offline(target, request):
        if request["operation"] == "read":
            raise SourceError("private path and secret must not be shown", -2146233087,
                              "rust_lab_loopback_snapshot", "lab_snapshot_unavailable")
        return original(target, request)
    channel.request = offline
    sources.poll_once(machine, machine["modules"][0], state)
    view = sources.status(mid)["origins"][0]
    assert view["status"] == "unavailable"
    assert "lab_snapshot_unavailable" in view["message"]
    assert "private path" not in view["message"] and "secret" not in view["message"]
    assert dest.sent[-1]["samples"][0]["quality"] == 8
    channel.request = original
    sources.poll_once(machine, machine["modules"][0], state)
    assert sources.status(mid)["status"] == "connected"
    assert dest.sent[-1]["samples"][0]["value"] == 7.0


def test_corrupt_discovery_catalog_does_not_replace_saved_configuration(lab):
    store, deploy, sources, channel, _ = lab
    before = store.state(False)
    sources.path.write_text("broken")
    reloaded = Sources(store, deploy, channel)
    assert reloaded.catalog_error and sources.path.read_text() == "broken"
    with pytest.raises(ManagementError): reloaded.discover("origin.invalid")
    assert store.state(False) == before


def test_bounded_read_window_isolates_pauses_and_recovers(lab, monkeypatch):
    store, deployments, sources, channel, dest = lab
    mid, _, _ = configure(lab)
    state, machine = deployments.machine(mid)
    module = machine['modules'][0]
    path = store.directory / 'relay-read-windows.json'
    clock = [100.]
    monkeypatch.setattr('opc_da_bridge.management.sources.time.time', lambda: clock[0])
    path.write_text(json.dumps({'schema_version': 1, 'windows': {module['id']: {
        'machine_id': mid, 'not_before_unix_ms': 100000, 'until_unix_ms': 110000}}}))
    channel.requests.clear()
    sources.poll_once(machine, module, state)
    assert sources.status(mid)['status'] == 'connected'
    assert channel.requests
    count = len(channel.requests)
    sent = len(dest.sent)
    clock[0] = 110.
    sources.poll_once(machine, module, state)
    assert sources.status(mid)['status'] == 'paused'
    assert len(channel.requests) == count and len(dest.sent) == sent
    assert sources._read_window(mid, 'another-module') == (True, None)
    clock[0] = 105.
    sources.poll_once(machine, module, state)
    assert sources.status(mid)['status'] == 'connected'
    assert len(channel.requests) > count


@pytest.mark.parametrize('content', ['invalid', '{"schema_version":1,"windows":[]}',
    '{"schema_version":1,"windows":{"selected":{"machine_id":"other","not_before_unix_ms":0,"until_unix_ms":1000}}}'])
def test_bounded_read_window_invalid_control_fails_closed(lab, content):
    store, _, sources, _, _ = lab
    (store.directory / 'relay-read-windows.json').write_text(content)
    assert sources._read_window('machine', 'selected')[0] is False


def test_bounded_window_expiring_during_source_call_does_not_deliver(lab, monkeypatch):
    store, deployments, sources, channel, dest = lab
    mid, _, _ = configure(lab)
    state, machine = deployments.machine(mid)
    module = machine['modules'][0]
    clock = [100.]
    monkeypatch.setattr('opc_da_bridge.management.sources.time.time', lambda: clock[0])
    (store.directory / 'relay-read-windows.json').write_text(json.dumps({
        'schema_version': 1, 'windows': {module['id']: {
            'machine_id': mid, 'not_before_unix_ms': 100000, 'until_unix_ms': 110000}}}))
    original = channel.request
    def request(*args):
        reply = original(*args)
        clock[0] = 110.
        return reply
    channel.request = request
    sources.poll_once(machine, module, state)
    assert sources.status(mid)['status'] == 'paused'
    assert not any(x.get('operation') == 'source-samples' for x in dest.sent)


def test_continuous_collection_can_stop_without_changing_catalog(lab, monkeypatch):
    store, deployments, sources, channel, dest = lab
    mid, _, _ = configure(lab)
    state, machine = deployments.machine(mid)
    module = machine['modules'][0]
    path = store.directory / 'relay-read-windows.json'
    control = {'schema_version': 1, 'windows': {module['id']: {
        'machine_id': mid, 'mode': 'continuous', 'enabled': True}}}
    path.write_text(json.dumps(control))
    before = store.state(False)
    # Continuous operation remains permitted after the bounded window limit.
    for clock in [100., 1000.]:
        monkeypatch.setattr('opc_da_bridge.management.sources.time.time', lambda: clock)
        sources.poll_once(machine, module, state)
        assert sources.status(mid)['status'] == 'connected'
    assert sources.status(mid)['origins'][0]['source_environment'] == 'real_read_only'
    assert sources._collection_interval(mid, dict(module, update_interval_s=5)) == 10
    assert sources._collection_interval(mid, dict(module, update_interval_s=20)) == 20
    assert sources._collection_interval('another-machine', dict(module, update_interval_s=5)) == 5
    requests, deliveries = len(channel.requests), len(dest.sent)
    control['windows'][module['id']]['enabled'] = False
    path.write_text(json.dumps(control))
    sources.poll_once(machine, module, state)
    assert sources.status(mid)['status'] == 'paused'
    assert len(channel.requests) == requests and len(dest.sent) == deliveries
    assert sources._read_window(mid, 'unrelated-module') == (True, None)
    assert store.state(False) == before


@pytest.mark.parametrize('entry', [
    {'machine_id': 'other', 'mode': 'continuous', 'enabled': True},
    {'machine_id': 'machine', 'mode': 'continuous', 'enabled': 'true'},
    {'machine_id': 'machine', 'mode': 'continuous'},
    {'machine_id': 'machine', 'mode': 'continuous', 'enabled': True, 'interval_s': 0},
    {'machine_id': 'machine', 'mode': 'continuous', 'enabled': True, 'interval_s': True},
    {'machine_id': 'machine', 'mode': 'continuous', 'enabled': True, 'interval_s': '10'},
    {'machine_id': 'machine', 'mode': 'unknown', 'enabled': True},
])
def test_continuous_collection_requires_explicit_permission_and_identity(lab, entry):
    store, _, sources, _, _ = lab
    (store.directory / 'relay-read-windows.json').write_text(json.dumps({
        'schema_version': 1, 'windows': {'selected': entry}}))
    assert sources._read_window('machine', 'selected')[0] is False
