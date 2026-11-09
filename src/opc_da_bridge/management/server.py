"""Loopback configurator with explicitly provisioned Windows laboratory agents."""
from __future__ import annotations

import json
import mimetypes
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from .agents import SimulatedAgent
from .sources import Sources
from .deployment import Deployments
from .store import ConfigurationError, KINDS, ManagementError, Store, _valid_host


MAX_BODY = 65536


def _validate_public_origin(origin):
    """Allow one explicitly configured proxy origin, never forwarded headers."""
    if origin is None:
        return None
    valid = isinstance(origin, str) and bool(origin) and len(origin) <= 2048
    if valid:
        valid = not any(c.isspace() or not c.isprintable() for c in origin)
    if valid:
        try:
            parts = urlsplit(origin)
            valid = (parts.scheme in ("http", "https") and bool(parts.hostname)
                     and _valid_host(parts.hostname)
                     and parts.port is not None and 1 <= parts.port <= 65535
                     and parts.username is None and parts.password is None
                     and not parts.path and not parts.query and not parts.fragment
                     and "?" not in origin and "#" not in origin)
        except ValueError:
            valid = False
    if not valid:
        raise ConfigurationError(
            "Origem pública inválida: use http://host:porta ou https://host:porta, "
            "sem credenciais, caminho, consulta ou fragmento.")
    return origin


class ManagementServer(ThreadingHTTPServer):
    daemon_threads = False
    allow_reuse_address = True

    def __init__(self, address, data_dir, static_dir=None, public_origin=None):
        if address[0] != "127.0.0.1":
            raise ConfigurationError("O backend aceita apenas 127.0.0.1. Use um proxy autenticado para acesso de rede.")
        self.public_origin = _validate_public_origin(public_origin)
        self.store = Store(data_dir)
        self.agent = SimulatedAgent()
        self.deployments = Deployments(self.store)
        self.store.deployments = self.deployments
        self.sources = Sources(self.store, self.deployments)
        self.static_dir = Path(static_dir or Path(__file__).with_name("static")).resolve()
        try:
            super().__init__(address, Handler)
            self.sources.start()
            self.store.event("info", "Gerenciamento", "Backend iniciado. Simulação e OPC DA via agentes explicitamente provisionados; consulte os estados confirmados.")
        except Exception:
            self.store.close()
            raise

    def server_close(self):
        self.sources.close()
        super().server_close()
        if not self.store.closed:
            try:
                self.store.event("info", "Gerenciamento", "Backend encerrado. Configuração persistida; nenhum agente Windows foi alterado.")
            finally:
                self.store.close()


def make_server(data_dir, host="127.0.0.1", port=8765, static_dir=None, public_origin=None):
    return ManagementServer((host, port), data_dir, static_dir, public_origin)


class Handler(BaseHTTPRequestHandler):
    server_version = "OPCBridgeManagement/0.1"
    sys_version = ""

    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def _mutation_json(self, status, result):
        self.server.agent.prune(self.server.store.variable_ids())
        return self._json(status, result)

    def log_message(self, format, *args):
        # Explicit, sanitized application events replace request URLs/body logs.
        pass

    def _headers(self, status, content_type, length):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()

    def _json(self, status, value):
        data = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self._headers(status, "application/json; charset=utf-8", len(data))
        self.wfile.write(data)

    def _check_request(self, mutation=False):
        allowed = {"127.0.0.1:%s" % self.server.server_port,
                   "localhost:%s" % self.server.server_port}
        if self.headers.get("Host", "") not in allowed:
            raise ManagementError("Host não autorizado para o laboratório local.", 403, "forbidden_host")
        if mutation:
            origin = self.headers.get("Origin")
            allowed_origins = {"http://" + value for value in allowed}
            if self.server.public_origin is not None:
                allowed_origins.add(self.server.public_origin)
            if origin is not None and origin not in allowed_origins:
                raise ManagementError("Origem não autorizada.", 403, "forbidden_origin")
            if self.headers.get("Sec-Fetch-Site") == "cross-site":
                raise ManagementError("Requisição de outro site recusada.", 403, "forbidden_origin")

    def _body(self):
        if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
            raise ManagementError("Use Content-Type application/json.", 415, "unsupported_media_type")
        if self.headers.get("Transfer-Encoding"):
            raise ManagementError("Transferência segmentada não suportada.")
        try:
            length = int(self.headers["Content-Length"])
        except (TypeError, ValueError):
            raise ManagementError("Content-Length obrigatório.", 411, "length_required")
        if length < 0 or length > MAX_BODY:
            raise ManagementError("Documento JSON excede 64 KiB.", 413, "body_too_large")
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"),
                              parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
        except (ValueError, UnicodeDecodeError):
            raise ManagementError("JSON inválido.")
        if not isinstance(data, dict):
            raise ManagementError("O documento deve ser um objeto JSON.")
        return data

    def _expected_revision(self):
        value = self.headers.get("If-Match")
        if value is None:
            raise ManagementError("Informe a revisão com If-Match.", 428, "revision_required")
        try:
            result = int(value.strip('"'))
            if result < 0:
                raise ValueError()
            return result
        except ValueError:
            raise ManagementError("If-Match deve conter a revisão numérica.")

    def _read_samples(self, variables):
        snapshot = self.server.sources.dashboard_samples_for(variables)
        remaining = [v for v in variables if v["id"] not in snapshot]
        normal = self.server.deployments.read(remaining, self.server.agent) if remaining else {
            "simulated": True, "applied_to_windows": False, "samples": []}
        ordinary = {s["variable_id"]: s for s in normal["samples"]}
        samples = [snapshot.get(v["id"], ordinary.get(v["id"])) for v in variables]
        return {"simulated": all(s.get("source_kind") != "opc_da" for s in samples),
                "applied_to_windows": any(s.get("applied_to_windows") is True for s in samples),
                "lab_snapshot": self.server.sources.dashboard_status(), "samples": samples}

    def _dispatch(self):
        mutation = self.command in ("POST", "PATCH", "DELETE")
        self._check_request(mutation)
        parsed = urlsplit(self.path)
        path = unquote(parsed.path)
        query = parse_qs(parsed.query, keep_blank_values=True)
        store = self.server.store
        if self.command == "POST" and path == "/api/v1/opc-da/discover":
            data = self._body()
            if set(data) != {"host"}: raise ManagementError("Informe somente o host de origem.")
            return self._json(200, self.server.sources.discover(data["host"]))
        source_status = re.fullmatch(r"/api/v1/machines/([^/]+)/source-status", path)
        if self.command == "GET" and source_status:
            return self._json(200, self.server.sources.status(source_status.group(1)))
        deployment = re.fullmatch(r"/api/v1/machines/([^/]+)/(deployment|probe|apply)", path)
        if deployment:
            identifier, operation = deployment.groups()
            if self.command == "GET" and operation == "deployment":
                _, machine = self.server.deployments.machine(identifier)
                return self._json(200, {"deployment": self.server.deployments.status(machine)})
            if self.command == "POST" and operation in ("probe", "apply"):
                if self._body(): raise ManagementError("A operação não aceita comandos ou parâmetros remotos.")
                if operation == "probe": return self._json(200, self.server.deployments.probe(identifier))
                self.server.sources.validate_destination(self.server.deployments.machine(identifier)[1])
                return self._json(202, self.server.deployments.start(identifier, self._expected_revision()))
        if self.command == "GET" and path == "/api/v1/state":
            state = store.state()
            state["lab_snapshot"] = self.server.sources.dashboard_status()
            return self._json(200, state)
        if self.command == "GET" and path == "/api/v1/lab-snapshot/status":
            return self._json(200, self.server.sources.dashboard_status())
        if self.command == "GET" and path == "/api/v1/health":
            state = store.state()
            # Backend health and historical ACKs do not assert a live Windows runtime.
            return self._json(200, {"status": "ok", "mode": state["mode"], "actual_agents_connected": 0,
                                    "health_scope": "management_backend", "agent_connection_mode": "on_demand_ssh",
                                    "applied_to_windows": state["applied_to_windows"],
                                    "text_log_available": not store.log_error, "revision": state["revision"]})
        if self.command == "GET" and path == "/api/v1/events":
            try:
                after = int(query.get("after", ["0"])[0])
                limit = int(query.get("limit", ["100"])[0])
                if after < 0 or not 1 <= limit <= 200:
                    raise ValueError()
            except ValueError:
                raise ManagementError("Parâmetros de eventos inválidos.")
            return self._json(200, store.events(after, limit))
        if self.command == "GET" and path == "/api/v1/samples":
            identifiers = query.get("ids", [""])[0].split(",")
            if identifiers == [""]:
                identifiers = []
            if len(identifiers) > 500 or len(identifiers) != len(set(identifiers)):
                raise ManagementError("Selecione até 500 variáveis distintas para monitorar.")
            return self._json(200, self._read_samples(store.variables(identifiers)))
        match = re.fullmatch(r"/api/v1/variables/([^/]+)/read", path)
        if self.command == "GET" and match:
            variable = store.variables([match.group(1)])[0]
            result = self._read_samples([variable])
            sample = result["samples"][0]
            store.event("info", variable["name"], "Leitura concluída: %s; qualidade %s." % (sample["origin"], sample["quality"]))
            return self._json(200, {"simulated": result["simulated"], "applied_to_windows": result["applied_to_windows"], "sample": sample})
        if self.command == "POST" and path == "/api/v1/simulator/availability":
            data = self._body()
            if set(data) != {"available"} or not isinstance(data["available"], bool):
                raise ManagementError("Informe apenas available: true ou false.")
            self.server.agent.set_available(data["available"])
            store.event("info" if data["available"] else "warning", "Agente simulado",
                        "Fonte simulada disponível." if data["available"] else "Fonte simulada indisponível; último valor permanece com qualidade ruim.")
            return self._json(200, {"simulated": True, "available": data["available"], "applied_to_windows": False})
        match = re.fullmatch(r"/api/v1/([a-z]+)(?:/([^/]+))?", path)
        if match and match.group(1) in KINDS and mutation:
            kind, identifier = match.groups()
            expected = self._expected_revision()
            if self.command == "POST" and identifier is None:
                data = self._body()
                if kind == "modules": self.server.sources.validate_selection(data)
                result = store.mutate("create", kind, data, expected)
                return self._mutation_json(201, result)
            if self.command == "PATCH" and identifier:
                data = self._body()
                if kind == "modules":
                    old = json.loads(store._node(kind, identifier)["payload"])
                    self.server.sources.validate_selection(dict(old, **data))
                result = store.mutate("update", kind, data, expected, identifier)
                return self._mutation_json(200, result)
            if self.command == "DELETE" and identifier:
                result = store.mutate("delete", kind, {}, expected, identifier)
                return self._mutation_json(200, result)
        if path.startswith("/api/") or self.command != "GET":
            raise ManagementError("Recurso não encontrado.", 404, "not_found")
        # Serve only files contained in the UI directory, never data-dir or source.
        if "\\" in path or "\x00" in path or ".." in path.split("/"):
            raise ManagementError("Caminho não permitido.", 403, "forbidden_path")
        relative = "index.html" if path == "/" else path.lstrip("/")
        target = (self.server.static_dir / relative).resolve()
        try:
            target.relative_to(self.server.static_dir)
        except ValueError:
            raise ManagementError("Caminho não permitido.", 403, "forbidden_path")
        if not target.is_file():
            raise ManagementError("Arquivo não encontrado.", 404, "not_found")
        content = target.read_bytes()
        mime = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        self._headers(200, mime + "; charset=utf-8" if mime.startswith("text/") or mime == "application/javascript" else mime, len(content))
        self.wfile.write(content)

    def _handle(self):
        try:
            self._dispatch()
        except ManagementError as exc:
            if self.command in ("POST", "PATCH", "DELETE") and exc.code in (
                    "validation_error", "duplicate_name", "duplicate_item_id", "revision_conflict"):
                self.server.store.event("warning", "Configuração", "Alteração recusada (%s); configuração anterior preservada." % exc.code)
            value = {"error": {"code": exc.code, "message": str(exc)}}
            if exc.revision is not None:
                value["revision"] = exc.revision
            self._json(exc.status, value)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            # Never include database paths, request bodies or stack traces in HTTP.
            try:
                self.server.store.event("error", "Gerenciamento", "Erro interno ao processar uma solicitação; configuração preservada.")
            except Exception:
                pass
            self._json(500, {"error": {"code": "internal_error", "message": "Erro interno. A configuração persistida foi preservada."}})

    do_GET = do_POST = do_PATCH = do_DELETE = do_PUT = do_OPTIONS = _handle
