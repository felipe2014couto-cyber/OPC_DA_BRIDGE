"""Real loopback HTTP tests; no Windows, OPC, ABB or PI connection is made."""
from __future__ import annotations

import http.client
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from opc_da_bridge.management.agents import SimulatedAgent
from opc_da_bridge.management.server import make_server
from opc_da_bridge.management.store import ConfigurationError, Store
from opc_da_bridge.management.sources import Sources

# Synthetic snapshot fixture values. Never use production or workstation identities in tests.
SNAPSHOT_HOST = "snapshot.example.invalid"
SNAPSHOT_PROGID = "Example.Cache.Server.1"
SNAPSHOT_CLSID = "{11111111-2222-3333-4444-555555555555}"
SNAPSHOT_AGENT_DIR = r"C:\Lab\Agent"
SNAPSHOT_ITEMS = {"Example.Numeric": "Double", "Example.Boolean": "Boolean"}



class Client:
    def __init__(self, server):
        self.server = server

    def call(self, method, path, data=None, revision=None, headers=None):
        request_headers = dict(headers or {})
        body = None
        if data is not None:
            body = json.dumps(data)
            request_headers.setdefault("Content-Type", "application/json")
        if revision is not None:
            request_headers["If-Match"] = str(revision)
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        conn.request(method, path, body=body, headers=request_headers)
        response = conn.getresponse()
        raw = response.read()
        status, content_type = response.status, response.getheader("Content-Type", "")
        response_headers = dict(response.getheaders())
        conn.close()
        result = json.loads(raw) if "application/json" in content_type else raw
        return status, result, response_headers

    def state(self):
        status, body, _ = self.call("GET", "/api/v1/state")
        assert status == 200
        return body

    def create(self, kind, data):
        status, body, _ = self.call("POST", "/api/v1/" + kind, data, self.state()["revision"])
        assert status == 201, body
        return body["state"]


@pytest.fixture
def app(tmp_path):
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<h1>Test UI</h1>")
    server = make_server(tmp_path / "data", port=0, static_dir=static)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield Client(server), tmp_path
    server.shutdown()
    server.server_close()
    thread.join(5)


def hierarchy(client):
    state = client.create("connections", {"name": "Conexão", "description": "Laboratório"})
    connection = state["connections"][0]
    state = client.create("machines", {"parent_id": connection["id"], "name": "Máquina 2", "host": "lab.example.invalid"})
    machine = state["connections"][0]["machines"][0]
    state = client.create("modules", {"parent_id": machine["id"], "name": "Módulo 1", "source_endpoint": "opc.tcp://source.example.invalid:4840/lab/"})
    module = state["connections"][0]["machines"][0]["modules"][0]
    state = client.create("folders", {"parent_id": module["id"], "name": "Pasta 1"})
    folder = state["connections"][0]["machines"][0]["modules"][0]["folders"][0]
    state = client.create("variables", {"parent_id": folder["id"], "name": "Pressão", "data_type": "Double", "initial_value": 6.25, "source_id": "Simulado.Pressao", "description": "Pressão simulada"})
    variable = state["connections"][0]["machines"][0]["modules"][0]["folders"][0]["variables"][0]
    return dict(zip(("connections", "machines", "modules", "folders", "variables"), (connection, machine, module, folder, variable)))


@pytest.mark.parametrize('kind,value', [('Double', 12.5), ('Integer', -2147483648),
                                       ('String', 'Ação\x00🚀'), ('Boolean', False)])
def test_four_scalar_types_persist_and_simulated_read_keeps_type(app, kind, value):
    client, _ = app
    nodes = hierarchy(client)
    state = client.create('variables', {'parent_id': nodes['folders']['id'], 'name': 'NewType',
                                       'data_type': kind, 'initial_value': value})
    variable = next(v for v in state['connections'][0]['machines'][0]['modules'][0]['folders'][0]['variables']
                    if v['name'] == 'NewType')
    code, result, _ = client.call('GET', '/api/v1/variables/%s/read' % variable['id'])
    assert code == 200 and result['sample']['value'] == value
    assert result['sample']['data_type'] == kind and variable['read_only'] is True


def test_http_full_hierarchy_create_edit_read_and_events(app):
    client, path = app
    assert client.state()["connections"] == []
    nodes = hierarchy(client)
    variable = nodes["variables"]
    assert variable["item_id"] == "Maquina_2.Modulo_1.Pasta_1.Pressao"
    assert variable["read_only"] is True
    initial_item = variable["item_id"]
    for kind, node in nodes.items():
        status, body, _ = client.call("PATCH", "/api/v1/%s/%s" % (kind, node["id"]), {"name": node["name"] + " alterado"}, client.state()["revision"])
        assert status == 200, body
        assert body["saved"] and body["applied_to_windows"] is False
    state = client.state()
    assert state["revision"] == 10
    assert state["mode"] == "simulated" and not state["applied_to_windows"]
    stored = state["connections"][0]["machines"][0]["modules"][0]["folders"][0]["variables"][0]
    assert stored["item_id"] == initial_item  # ancestor/name edits never silently rename IDs
    status, read, _ = client.call("GET", "/api/v1/variables/%s/read" % variable["id"])
    assert status == 200 and read["simulated"] and not read["applied_to_windows"]
    assert read["sample"]["value"] == 6.25 and read["sample"]["quality_code"] == 192
    assert read["sample"]["source_timestamp"].endswith("Z")
    assert read["sample"]["origin"] == "Agente simulado Linux"
    status, events, _ = client.call("GET", "/api/v1/events?limit=100")
    assert status == 200 and any("Leitura" in e["message"] for e in events["events"])
    assert all(e["simulated"] for e in events["events"])
    assert "configuração salva" in (path / "data" / "management.log").read_text()


def test_variable_can_move_within_same_machine_without_changing_identity(app):
    client, _ = app
    nodes = hierarchy(client)
    initial_id = nodes["variables"]["id"]
    initial_item_id = nodes["variables"]["item_id"]
    state = client.create("modules", {"parent_id": nodes["machines"]["id"], "name": "Origem OPC DA"})
    source_module = state["connections"][0]["machines"][0]["modules"][1]
    state = client.create("folders", {"parent_id": source_module["id"], "name": "Bindings"})
    source_folder = state["connections"][0]["machines"][0]["modules"][1]["folders"][0]
    status, response, _ = client.call("PATCH", "/api/v1/variables/" + initial_id,
        {"parent_id": source_folder["id"], "source_id": "Example.Numeric.Double"}, client.state()["revision"])
    assert status == 200, response
    state = client.state()
    found = [(m, f, v) for connection in state["connections"] for machine in connection["machines"]
        for m in machine["modules"] for f in m["folders"] for v in f["variables"] if v["id"] == initial_id]
    assert len(found) == 1
    module, folder, variable = found[0]
    assert module["name"] == "Origem OPC DA" and folder["name"] == "Bindings"
    assert variable["item_id"] == initial_item_id and variable["source_id"] == "Example.Numeric.Double"

    state = client.create("connections", {"name": "Outra máquina"})
    connection = state["connections"][-1]
    state = client.create("machines", {"parent_id": connection["id"], "name": "Destino 2", "host": "other.example.invalid"})
    machine = state["connections"][-1]["machines"][0]
    state = client.create("modules", {"parent_id": machine["id"], "name": "Outro módulo"})
    other_module = state["connections"][-1]["machines"][0]["modules"][0]
    state = client.create("folders", {"parent_id": other_module["id"], "name": "Outra pasta"})
    other_folder = state["connections"][-1]["machines"][0]["modules"][0]["folders"][0]
    revision = state["revision"]
    status, _, _ = client.call("PATCH", "/api/v1/variables/" + initial_id,
        {"parent_id": other_folder["id"]}, revision)
    assert status == 400 and client.state()["revision"] == revision


def test_persistent_lab_snapshot_api_is_simulated_and_fails_closed(app):
    client, path = app
    state = client.create("connections", {"name": "Laboratório"})
    connection = state["connections"][0]
    state = client.create("machines", {"parent_id": connection["id"], "name": "LabMachine", "host": SNAPSHOT_HOST})
    machine = state["connections"][0]["machines"][0]
    state = client.create("modules", {"parent_id": machine["id"], "name": "TESTE", "source_kind": "simulated"})
    module = state["connections"][0]["machines"][0]["modules"][0]
    state = client.create("folders", {"parent_id": module["id"], "name": "TESTE1"})
    folder = state["connections"][0]["machines"][0]["modules"][0]["folders"][0]
    state = client.create("variables", {"parent_id": folder["id"], "name": "TESTE2",
        "item_id": "Lab.Numeric", "source_id": "Lab.Numeric",
        "data_type": "Double", "initial_value": 28})
    number = state["connections"][0]["machines"][0]["modules"][0]["folders"][0]["variables"][0]
    state = client.create("variables", {"parent_id": folder["id"], "name": "DIGITAL",
        "item_id": "Lab.Boolean", "source_id": "Lab.Boolean",
        "data_type": "Boolean", "initial_value": True})
    variables = state["connections"][0]["machines"][0]["modules"][0]["folders"][0]["variables"]
    boolean = next(v for v in variables if v["name"] == "DIGITAL")
    profile = {"schema_version": 1, "enabled": True, "profile_id": "snapshot-api-test",
        "machine_id": machine["id"], "source_host": SNAPSHOT_HOST,
        "prog_id": SNAPSHOT_PROGID, "clsid": SNAPSHOT_CLSID,
        "interval_s": 5, "bindings": [
            {"variable_id": number["id"], "published_item_id": number["item_id"], "source_item_id": number["source_id"], "data_type": "Double"},
            {"variable_id": boolean["id"], "published_item_id": boolean["item_id"], "source_item_id": boolean["source_id"], "data_type": "Boolean"}]}
    data_dir = path / "data"
    (data_dir / "lab-dashboard-snapshot.json").write_text(json.dumps(profile))
    (data_dir / "source-agents.json").write_text(json.dumps({"sources": {SNAPSHOT_HOST: {
        "host": SNAPSHOT_HOST, "expected_hostname": "snapshot-lab", "expected_address": SNAPSHOT_HOST,
        "expected_process_architecture": 32, "enabled": True, "read_transport": "rust_lab_loopback_snapshot",
        "agent_dir": SNAPSHOT_AGENT_DIR,
        "lab_snapshot": {"enabled": True, "prog_id": SNAPSHOT_PROGID,
            "clsid": SNAPSHOT_CLSID, "port": 49321,
            "items": [{"source_item_id": item, "data_type": SNAPSHOT_ITEMS[item]}
                      for item in SNAPSHOT_ITEMS]}}}}))

    class FakeSnapshot:
        fail = False
        def request(self, target, request):
            if self.fail: raise RuntimeError("simulated relay outage")
            return {"ok": True, "identity": {"hostname": "snapshot-lab", "addresses": [target["host"]], "process_architecture": 32},
                "transport": "rust_lab_loopback_snapshot", "read_method": "GET /v1/snapshot over SSH-local loopback",
                "source_pid": 2345, "source_session": 1,
                "samples": [{**item, "value": False if item["data_type"] == "Boolean" else 7.0,
                    "quality": 192, "source_timestamp_filetime": 116444736000000000,
                    "source_vartype": 11 if item["data_type"] == "Boolean" else 5} for item in request["items"]]}

    channel = FakeSnapshot()
    client.server.sources.channel = channel
    revision = client.state()["revision"]
    client.server.sources._poll_dashboard_snapshot()
    status, response, _ = client.call("GET", "/api/v1/samples?ids=" + number["id"] + "," + boolean["id"])
    assert status == 200 and response["simulated"] is True and response["applied_to_windows"] is False
    n, b = response["samples"]
    assert n["value"] == 7.0 and n["data_type"] == "Double" and n["source_vartype"] == 5
    assert b["value"] is False and b["data_type"] == "Boolean" and b["source_vartype"] == 11
    assert all(s["quality_code"] == 192 and s["source_timestamp"] == "1970-01-01T00:00:00.000000Z" for s in (n, b))
    assert all(s["lab_snapshot"] and "hresult" not in s for s in (n, b))
    assert client.call("GET", "/api/v1/lab-snapshot/status")[1]["status"] == "connected"

    channel.fail = True
    client.server.sources._poll_dashboard_snapshot()
    _, failed, _ = client.call("GET", "/api/v1/samples?ids=" + number["id"] + "," + boolean["id"])
    assert all(s["value"] is None and s["quality_code"] == 8 and s["source_timestamp"] is None for s in failed["samples"])
    assert all("hresult" not in s for s in failed["samples"])
    channel.fail = False
    client.server.sources._poll_dashboard_snapshot()
    _, recovered, _ = client.call("GET", "/api/v1/samples?ids=" + number["id"] + "," + boolean["id"])
    assert recovered["samples"][0]["value"] == 7.0 and recovered["samples"][0]["quality_code"] == 192
    assert client.state()["revision"] == revision


@pytest.mark.parametrize("kind", ["variables", "folders", "modules", "machines", "connections"])
def test_delete_each_level_cascades(app, kind):
    client, _ = app
    nodes = hierarchy(client)
    status, body, _ = client.call("DELETE", "/api/v1/%s/%s" % (kind, nodes[kind]["id"]), revision=client.state()["revision"])
    assert status == 200
    assert body["state"]["revision"] == 6
    assert client.call("GET", "/api/v1/variables/%s/read" % nodes["variables"]["id"])[0] == 404
    if kind == "connections":
        assert body["state"]["connections"] == []


def test_validation_duplicate_scope_and_no_partial_updates(app):
    client, _ = app
    nodes = hierarchy(client)
    revision = client.state()["revision"]
    invalid = [
        ("connections", {"name": " conexão "}, 409),
        ("connections", {"name": "Conexa\u0303o"}, 409),
        ("connections", {"name": ""}, 400),
        ("connections", {"name": "invalid\ud800"}, 400),
        ("connections", {"name": "Outro", "execute": "anything"}, 400),
        ("machines", {"parent_id": nodes["connections"]["id"], "name": "Outra", "host": "http://not-a-host/"}, 400),
        ("machines", {"parent_id": nodes["connections"]["id"], "name": "Outra", "host": "not a host!"}, 400),
        ("modules", {"parent_id": nodes["machines"]["id"], "name": "Outro", "source_endpoint": "opc.tcp://user:password@example.invalid:4840/"}, 400),
        ("modules", {"parent_id": nodes["machines"]["id"], "name": "Outro", "source_endpoint": "opc.tcp://example.invalid/"}, 400),
        ("modules", {"parent_id": nodes["machines"]["id"], "name": "Outro", "source_endpoint": "opc.tcp://invalid!:4840/"}, 400),
        ("variables", {"parent_id": nodes["folders"]["id"], "name": "Bool", "data_type": "Boolean", "initial_value": "true"}, 400),
        ("variables", {"parent_id": nodes["folders"]["id"], "name": "Double", "data_type": "Double", "initial_value": True}, 400),
        ("variables", {"parent_id": nodes["folders"]["id"], "name": "Double", "data_type": "Double", "initial_value": float("nan")}, 400),
        ("variables", {"parent_id": nodes["folders"]["id"], "name": "Double", "data_type": "Double", "initial_value": 10 ** 400}, 400),
    ]
    for kind, data, expected in invalid:
        status, body, _ = client.call("POST", "/api/v1/" + kind, data, revision)
        assert status == expected, body
        assert client.state()["revision"] == revision
    # Same folder name in another module is allowed; duplicate Item ID in the
    # same destination remains forbidden even when it is in another folder.
    state = client.create("folders", {"parent_id": nodes["modules"]["id"], "name": "Pasta 2"})
    folder = state["connections"][0]["machines"][0]["modules"][0]["folders"][1]
    status, _, _ = client.call("POST", "/api/v1/variables", {"parent_id": folder["id"], "name": "Outra", "item_id": nodes["variables"]["item_id"], "data_type": "Double", "initial_value": 1}, state["revision"])
    assert status == 409
    status, _, _ = client.call("PATCH", "/api/v1/folders/" + folder["id"], {"parent_id": "root"}, state["revision"])
    assert status == 400


def test_boolean_monitoring_offline_and_new_sample_recovery(app):
    client, _ = app
    nodes = hierarchy(client)
    state = client.create("variables", {"parent_id": nodes["folders"]["id"], "name": "Bomba", "data_type": "Boolean", "initial_value": True})
    variables = state["connections"][0]["machines"][0]["modules"][0]["folders"][0]["variables"]
    ids = ",".join(v["id"] for v in variables)
    status, online, _ = client.call("GET", "/api/v1/samples?ids=" + ids)
    assert status == 200 and online["simulated"]
    assert online["samples"][1]["value"] is True
    assert online["samples"][1]["data_type"] == "Boolean"
    assert client.call("POST", "/api/v1/simulator/availability", {"available": False})[0] == 200
    bad = client.call("GET", "/api/v1/samples?ids=" + ids)[1]["samples"]
    bad_repeat = client.call("GET", "/api/v1/samples?ids=" + ids)[1]["samples"]
    assert bad == bad_repeat
    assert all(s["quality_code"] == 8 for s in bad)
    assert [s["source_timestamp"] for s in bad] == [s["source_timestamp"] for s in online["samples"]]
    assert client.call("POST", "/api/v1/simulator/availability", {"available": True})[0] == 200
    recovered = client.call("GET", "/api/v1/samples?ids=" + ids)[1]["samples"]
    assert all(s["quality_code"] == 192 for s in recovered)
    assert recovered[0]["source_timestamp"] >= bad[0]["source_timestamp"]


def test_simulator_invalid_timestamp_and_no_initial_good():
    agent = SimulatedAgent(clock=lambda: None)
    variable = {"id": "a", "item_id": "M.F.A", "source_id": "sim:a", "data_type": "Boolean", "initial_value": False}
    sample = agent.read([variable])[0]
    assert sample["quality_code"] == 64 and sample["source_timestamp"] is None
    agent.set_available(False)
    before = agent.read([dict(variable, id="b")])[0]
    assert before["value"] is None and before["quality_code"] == 8


def test_revision_required_conflict_and_concurrent_edit(app):
    client, _ = app
    assert client.call("POST", "/api/v1/connections", {"name": "A"})[0] == 428
    revision = client.state()["revision"]
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda name: client.call("POST", "/api/v1/connections", {"name": name}, revision)[0], ("A", "B")))
    assert sorted(results) == [201, 409]
    assert len(client.state()["connections"]) == 1
    status, body, _ = client.call("POST", "/api/v1/connections", {"name": "C"}, revision)
    assert status == 409 and body["revision"] == revision + 1


def test_persistence_across_real_server_restart_and_invalid_repository(tmp_path):
    static = tmp_path / "static"
    static.mkdir()
    data = tmp_path / "data"
    first = make_server(data, port=0, static_dir=static)
    thread = threading.Thread(target=first.serve_forever, daemon=True)
    thread.start()
    client = Client(first)
    nodes = hierarchy(client)
    before = client.state()
    first.shutdown(); first.server_close(); thread.join(5)
    second = make_server(data, port=0, static_dir=static)
    thread = threading.Thread(target=second.serve_forever, daemon=True)
    thread.start()
    client = Client(second)
    assert client.state() == before
    assert client.call("GET", "/api/v1/variables/%s/read" % nodes["variables"]["id"])[1]["simulated"]
    second.shutdown(); second.server_close(); thread.join(5)
    db = sqlite3.connect(str(data / "management.sqlite3"))
    db.execute("UPDATE nodes SET payload=? WHERE id=?", ('{"name":"Pressao","data_type":"Double","initial_value":"invalid","source_id":"sim","item_id":"x","description":""}', nodes["variables"]["id"]))
    db.commit(); db.close()
    original = (data / "management.sqlite3").read_bytes()
    with pytest.raises(ConfigurationError):
        make_server(data, port=0)
    assert (data / "management.sqlite3").read_bytes() == original
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "management.sqlite3").write_bytes(b"this is not sqlite")
    with pytest.raises(ConfigurationError):
        Store(broken)
    assert (broken / "management.sqlite3").read_bytes() == b"this is not sqlite"


def test_http_origin_host_path_and_mutation_surface(app):
    client, _ = app
    status, _, headers = client.call("GET", "/")
    assert status == 200 and "frame-ancestors 'none'" in headers["Content-Security-Policy"]
    assert client.call("GET", "/%2e%2e/management.sqlite3")[0] == 403
    assert client.call("GET", "/api/v1/state", headers={"Host": "attacker.example"})[0] == 403
    assert client.call("POST", "/api/v1/connections", {"name": "A"}, 0, {"Origin": "https://attacker.example"})[0] == 403
    assert client.call("POST", "/api/v1/connections", {"name": "A"}, 0, {"Content-Type": "text/plain"})[0] == 415
    assert client.call("POST", "/api/v1/execute", {"command": "not exposed"})[0] == 404
    assert client.call("POST", "/api/v1/connections", {"name": "A", "description": "x" * 70000}, 0)[0] == 413
    assert client.state()["revision"] == 0
    with pytest.raises(ConfigurationError):
        make_server("unused", host="0.0.0.0", port=0)


def test_cache_pruned_and_config_error_recorded(app):
    client, path = app
    nodes = hierarchy(client)
    identifier = nodes["variables"]["id"]
    assert client.call("GET", "/api/v1/variables/%s/read" % identifier)[0] == 200
    assert identifier in client.server.agent.cache
    status, _, _ = client.call("PATCH", "/api/v1/variables/" + identifier, {"initial_value": "not numeric"}, client.state()["revision"])
    assert status == 400
    events = client.call("GET", "/api/v1/events")[1]["events"]
    assert any(e["level"] == "warning" and "Alteração recusada" in e["message"] for e in events)
    assert "not numeric" not in (path / "data" / "management.log").read_text()
    assert client.call("DELETE", "/api/v1/variables/" + identifier, revision=client.state()["revision"])[0] == 200
    assert identifier not in client.server.agent.cache


def test_explicit_proxy_origin_allowed_without_trusting_forwarded_headers(tmp_path):
    origin = "http://management.example.invalid:9000"
    server = make_server(tmp_path / "proxy-data", port=0, public_origin=origin)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = Client(server)
    try:
        status, result, _ = client.call(
            "POST", "/api/v1/connections", {"name": "Proxy"}, 0, {"Origin": origin})
        assert status == 201 and result["saved"]
        assert client.call(
            "POST", "/api/v1/connections", {"name": "Outro"}, 1,
            {"Origin": "http://other.example.invalid:9000"})[0] == 403
        assert client.call(
            "POST", "/api/v1/connections", {"name": "Outro"}, 1,
            {"Origin": "https://other.example.invalid:9000",
             "X-Forwarded-Host": "management.example.invalid:9000",
             "X-Forwarded-Proto": "http",
             "Forwarded": "host=management.example.invalid:9000;proto=http"})[0] == 403
        # The proxy must still send the actual loopback Host, not the public Host.
        assert client.call(
            "POST", "/api/v1/connections", {"name": "Outro"}, 1,
            {"Host": "management.example.invalid:9000", "Origin": origin})[0] == 403
        loopback_origin = "http://127.0.0.1:%s" % server.server_port
        assert client.call(
            "POST", "/api/v1/connections", {"name": "Local"}, 1,
            {"Origin": loopback_origin})[0] == 201
        assert client.state()["revision"] == 2
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


@pytest.mark.parametrize("origin", [
    "", "management.example.invalid:8765", "ftp://management.example.invalid:8765",
    "http://management.example.invalid", "http://:8765", "http://bad!:8765",
    "http://user:secret@management.example.invalid:8765",
    "http://management.example.invalid:0", "http://management.example.invalid:65536",
    "http://management.example.invalid:8765/", "http://management.example.invalid:8765/path",
    "http://management.example.invalid:8765?query=1", "http://management.example.invalid:8765#fragment",
    "http://management.example.invalid:8765?", "http://management.example.invalid:8765#",
    " http://management.example.invalid:8765", "http://management.example.invalid:8765\n", 42,
])
def test_invalid_public_origin_rejected_before_repository_created(tmp_path, origin):
    data = tmp_path / "must-not-be-created"
    with pytest.raises(ConfigurationError, match="Origem pública inválida"):
        make_server(data, port=0, public_origin=origin)
    assert not data.exists()


@pytest.mark.parametrize("cli_origin", [None, "https://proxy.example.invalid:9443"])
def test_cli_public_origin_environment_and_override(monkeypatch, tmp_path, cli_origin):
    from opc_da_bridge.management import __main__ as entrypoint

    configured = {}
    environment_origin = "http://proxy.example.invalid:9000"
    monkeypatch.setenv("OPC_BRIDGE_PUBLIC_ORIGIN", environment_origin)

    class FakeServer:
        server_port = 8765

        def serve_forever(self):
            pass

        def server_close(self):
            configured["closed"] = True

    def fake_server(data_dir, host, port, **kwargs):
        configured.update(kwargs)
        return FakeServer()

    monkeypatch.setattr(entrypoint, "make_server", fake_server)
    argv = ["opc-management", "--data-dir", str(tmp_path / "unused")]
    if cli_origin:
        argv.extend(["--public-origin", cli_origin])
    monkeypatch.setattr("sys.argv", argv)
    assert entrypoint.main() == 0
    assert configured["public_origin"] == (cli_origin or environment_origin)
    assert configured["closed"]


def test_http_selected_machine_deployment_requires_ack_and_revision(app):
    from tests.test_management_deployment import Destinations
    client, path = app
    nodes = hierarchy(client)
    machine = nodes['machines']
    target = {'host': machine['host'], 'expected_hostname': 'LAB', 'machine_id': machine['id']}
    (client.server.store.directory / 'agents.json').write_text(json.dumps({'schema_version': 1, 'targets': {machine['id']: target}}))
    remote = Destinations()
    client.server.deployments.channel = remote
    route = '/api/v1/machines/' + machine['id']
    assert client.call('POST', route + '/apply', {}, 0)[0] == 409
    assert client.call('POST', route + '/probe', {'command': 'anything'})[0] == 400
    assert client.call('POST', route + '/probe', {})[1]['deployment']['identity_hostname'] == 'LAB'
    code, body, _ = client.call('POST', route + '/apply', {}, client.state()['revision'])
    assert code == 202
    client.server.deployments.jobs[machine['id']].join(5)
    deployment = client.call('GET', route + '/deployment')[1]['deployment']
    assert deployment['status'] == 'applied'
    assert deployment['receipt']['variable_count'] == 1
    health = client.call('GET', '/api/v1/health')[1]
    assert health['applied_to_windows'] is True and health['health_scope'] == 'management_backend'
    result = client.call('GET', '/api/v1/variables/' + nodes['variables']['id'] + '/read')
    assert result[0] == 200 and result[1]['applied_to_windows'] is True
    assert result[1]['sample']['origin'] == 'Agente Windows — fonte simulada'
