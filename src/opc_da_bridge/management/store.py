"""Transactional configuration repository. No network or Windows integration."""
from __future__ import annotations

import datetime as dt
import ipaddress
import json
import math
import re
import sqlite3
import sys
import threading
import unicodedata
import uuid
from pathlib import Path
from urllib.parse import urlsplit


KINDS = ("connections", "machines", "modules", "folders", "variables")
PARENT = dict(zip(KINDS, ("root", "connections", "machines", "modules", "folders")))
CHILD = dict(zip(KINDS[:-1], KINDS[1:]))
FIELDS = {
    "connections": {"name", "description"},
    "machines": {"name", "host"},
    "modules": {"name", "source_endpoint", "source_kind", "source_host", "source_progid", "source_clsid", "source_discovery_id", "update_interval_s"},
    "folders": {"name"},
    "variables": {"name", "item_id", "source_id", "data_type", "source_data_type", "source_conversion", "description", "initial_value"},
}


def utc_now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class ManagementError(Exception):
    def __init__(self, message, status=400, code="validation_error", revision=None):
        super().__init__(message)
        self.status, self.code, self.revision = status, code, revision


class ConfigurationError(RuntimeError):
    """The existing local repository cannot be safely used; never overwrite it."""


def _strict_json(value):
    return json.loads(value, parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))


def _text(value, field, required=False, maximum=2000):
    if not isinstance(value, str):
        raise ManagementError("O campo %s deve ser texto." % field)
    value = value.strip()
    if (required and not value) or len(value) > maximum or any(
            unicodedata.category(c) in ("Cc", "Cf", "Cs") for c in value):
        raise ManagementError("Informe %s válido (máximo %d caracteres)." % (field, maximum))
    return value


def _segment(name):
    value = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9_]+", "_", value).strip("_") or "Item"


def _valid_host(host):
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        # A malformed dotted-decimal IP must not silently become a DNS name.
        if re.fullmatch(r"[0-9.]+", host) and "." in host:
            return False
        return bool(re.fullmatch(r"(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*\.?", host))


class Store:
    def __init__(self, data_dir):
        self.directory = Path(data_dir)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "management.sqlite3"
        existed = self.path.exists() and self.path.stat().st_size > 0
        self.lock = threading.RLock()
        self.db = None
        self.closed = False
        self.log_error = False
        self.log_path = self.directory / "management.log"
        try:
            self.db = sqlite3.connect(str(self.path), check_same_thread=False, timeout=10)
            self.db.row_factory = sqlite3.Row
            self.db.execute("PRAGMA foreign_keys=ON")
            if existed:
                tables = {r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not {"metadata", "nodes", "events"}.issubset(tables):
                    raise ConfigurationError("Banco existente não reconhecido; arquivo preservado.")
                if self.db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise ConfigurationError("Falha de integridade no banco; arquivo preservado.")
            else:
                self.db.executescript("""
                    CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                    INSERT INTO metadata VALUES('schema_version','1'),('revision','0');
                    CREATE TABLE nodes(
                        id TEXT PRIMARY KEY, kind TEXT NOT NULL,
                        parent_id TEXT REFERENCES nodes(id) ON DELETE CASCADE,
                        name_key TEXT NOT NULL, payload TEXT NOT NULL,
                        scope_machine TEXT, item_id TEXT,
                        UNIQUE(kind,parent_id,name_key), UNIQUE(scope_machine,item_id));
                    INSERT INTO nodes(id,kind,parent_id,name_key,payload)
                        VALUES('root','root',NULL,'root','{}');
                    CREATE TABLE events(id INTEGER PRIMARY KEY AUTOINCREMENT,
                        time TEXT NOT NULL, level TEXT NOT NULL, origin TEXT NOT NULL,
                        message TEXT NOT NULL);
                """)
                self.db.commit()
            if self.db.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()[0] != "1":
                raise ConfigurationError("Versão de configuração não suportada; banco preservado.")
            int(self.db.execute("SELECT value FROM metadata WHERE key='revision'").fetchone()[0])
            if list(self.db.execute("PRAGMA foreign_key_check")):
                raise ConfigurationError("Relações inválidas no banco; arquivo preservado.")
            for row in self.db.execute("SELECT * FROM nodes WHERE kind!='root'").fetchall():
                payload = _strict_json(row["payload"])
                normalized = self._validate(row["kind"], payload, row["parent_id"], row["id"])
                if normalized != payload or row["name_key"] != normalized["name"].casefold():
                    raise ConfigurationError("Configuração armazenada inválida; banco preservado.")
                if row["kind"] == "variables" and (
                        row["scope_machine"] != self._machine_for_folder(row["parent_id"])
                        or row["item_id"] != normalized["item_id"]):
                    raise ConfigurationError("Escopo de variável armazenada inválido; banco preservado.")
            with self.log_path.open("a", encoding="utf-8"):
                pass
        except Exception as exc:
            if self.db is not None:
                self.db.close()
            if isinstance(exc, ConfigurationError):
                raise
            raise ConfigurationError("Não foi possível abrir a configuração local; arquivo preservado: %s" % exc) from exc

    def close(self):
        with self.lock:
            if not self.closed:
                self.closed = True
                self.db.close()

    def revision(self):
        return int(self.db.execute("SELECT value FROM metadata WHERE key='revision'").fetchone()[0])

    def _node(self, kind, identifier):
        row = self.db.execute("SELECT * FROM nodes WHERE id=? AND kind=?", (identifier, kind)).fetchone()
        if row is None:
            raise ManagementError("Elemento não encontrado.", 404, "not_found")
        return row

    def _validate(self, kind, data, parent_id, identifier=None):
        if kind not in KINDS or not isinstance(data, dict):
            raise ManagementError("Tipo de elemento ou documento inválido.")
        unknown = set(data) - FIELDS[kind]
        if unknown:
            raise ManagementError("Campos desconhecidos: %s." % ", ".join(sorted(unknown)))
        self._node(PARENT[kind], parent_id)
        result = {"name": unicodedata.normalize("NFC", _text(data.get("name", ""), "nome", True, 80))}
        duplicate = self.db.execute("SELECT id FROM nodes WHERE kind=? AND parent_id=? AND name_key=? AND id!=?",
                                    (kind, parent_id, result["name"].casefold(), identifier or "")).fetchone()
        if duplicate:
            raise ManagementError("Já existe um elemento com esse nome neste nível.", 409, "duplicate_name")
        if kind == "connections":
            result["description"] = _text(data.get("description", ""), "descrição")
        elif kind == "machines":
            host = _text(data.get("host", ""), "endereço/IP da máquina", True, 253)
            if not _valid_host(host):
                raise ManagementError("Informe um IP ou hostname válido, sem protocolo ou porta.")
            result["host"] = host
        elif kind == "modules":
            endpoint = _text(data.get("source_endpoint", ""), "endpoint OPC UA", maximum=1024)
            if endpoint:
                try:
                    parts = urlsplit(endpoint)
                    valid = (parts.scheme == "opc.tcp" and bool(parts.hostname)
                             and _valid_host(parts.hostname)
                             and parts.port is not None and 1 <= parts.port <= 65535
                             and parts.username is None and parts.password is None
                             and not parts.query and not parts.fragment
                             and not any(c.isspace() for c in endpoint))
                except ValueError:
                    valid = False
                if not valid:
                    raise ManagementError("Use endpoint completo opc.tcp://host:porta/caminho, sem credenciais.")
            result["source_endpoint"] = endpoint
            if "source_kind" in data:
                source_kind = data["source_kind"]
                if source_kind not in ("simulated", "opc_da"):
                    raise ManagementError("Escolha fonte simulada ou OPC DA.")
                result["source_kind"] = source_kind
                if source_kind == "opc_da":
                    for key in ("source_host", "source_progid", "source_clsid", "source_discovery_id"):
                        result[key] = _text(data.get(key, ""), key, True, 253)
                    if not _valid_host(result["source_host"]):
                        raise ManagementError("IP/hostname de origem inválido.")
                    if not re.fullmatch(r"[A-Za-z0-9_. -]{1,200}", result["source_progid"]):
                        raise ManagementError("ProgID inválido.")
                    if not re.fullmatch(r"\{[0-9A-Fa-f-]{36}\}", result["source_clsid"]):
                        raise ManagementError("CLSID inválido.")
                    interval = data.get("update_interval_s", 10)
                    if isinstance(interval, bool) or not isinstance(interval, int) or not 5 <= interval <= 3600:
                        raise ManagementError("Intervalo de leitura deve ser inteiro entre 5 e 3600 segundos.")
                    result["update_interval_s"] = interval
        elif kind == "variables":
            data_type = data.get("data_type")
            value = data.get("initial_value")
            if data_type not in ("Boolean", "Double"):
                raise ManagementError("Tipo deve ser Boolean ou Double.")
            if data_type == "Boolean":
                if not isinstance(value, bool):
                    raise ManagementError("O valor inicial Boolean deve ser true ou false.")
            else:
                try:
                    valid_number = (not isinstance(value, bool) and isinstance(value, (int, float))
                                    and math.isfinite(value))
                except OverflowError:
                    valid_number = False
                if not valid_number:
                    raise ManagementError("O valor inicial Double deve ser um número finito.")
            if data_type == "Double":
                value = float(value)
            folder = self._node("folders", parent_id)
            module = self._node("modules", folder["parent_id"])
            machine = self._node("machines", module["parent_id"])
            source_data_type = data.get("source_data_type")
            source_conversion = data.get("source_conversion")
            if source_data_type is not None or source_conversion is not None:
                if (module["payload"] and _strict_json(module["payload"]).get("source_kind") == "opc_da"
                        and data_type == "Double" and source_data_type == "Float"
                        and source_conversion == "float_to_double"):
                    result.update(source_data_type="Float", source_conversion="float_to_double")
                else:
                    raise ManagementError("Conversão permitida nesta etapa: OPC DA Float (VT_R4) para publicado Double (VT_R8).")
            proposed = data.get("item_id")
            if proposed is None:
                names = [_strict_json(r["payload"])["name"] for r in (machine, module, folder)] + [result["name"]]
                proposed = ".".join(_segment(n) for n in names)
            item_id = _text(proposed, "Item ID OPC DA", True, 512)
            if any(c.isspace() for c in item_id):
                raise ManagementError("Item ID não pode conter espaços.")
            if self.db.execute("SELECT id FROM nodes WHERE scope_machine=? AND item_id=? AND id!=?",
                               (machine["id"], item_id, identifier or "")).fetchone():
                raise ManagementError("Esse Item ID já existe na máquina de destino.", 409, "duplicate_item_id")
            source_id = data.get("source_id", result["name"])
            if not isinstance(source_id, str) or not source_id or len(source_id) > 1024 or any(unicodedata.category(c) in ("Cc", "Cf", "Cs") for c in source_id):
                raise ManagementError("Item ID de origem inválido (máximo 1024 caracteres).")
            result.update(data_type=data_type, initial_value=value, item_id=item_id,
                          source_id=data.get("source_id", result["name"]),
                          description=_text(data.get("description", ""), "descrição"))
        return result

    def _machine_for_folder(self, folder_id):
        folder = self._node("folders", folder_id)
        module = self._node("modules", folder["parent_id"])
        return module["parent_id"]

    def state(self, include_deployment=True):
        with self.lock:
            rows = self.db.execute("SELECT * FROM nodes WHERE kind!='root' ORDER BY rowid").fetchall()
            grouped = {}
            for row in rows:
                node = dict(id=row["id"], **_strict_json(row["payload"]))
                if row["kind"] == "variables":
                    node["read_only"] = True
                else:
                    node[CHILD[row["kind"]]] = []
                grouped[row["id"]] = node
            roots = []
            for row in rows:
                node = grouped[row["id"]]
                if row["kind"] == "connections":
                    roots.append(node)
                else:
                    grouped[row["parent_id"]][row["kind"]].append(node)
            state = {"schema_version": 1, "revision": self.revision(), "mode": "configured_sources" if any(m.get("source_kind") == "opc_da" for c in roots for d in c["machines"] for m in d["modules"]) else "simulated",
                    "applied_to_windows": False, "deployment_status": "management_only_pending",
                    "connections": roots}
            manager = getattr(self, "deployments", None)
            return manager.decorate(state) if manager and include_deployment else state

    def _event_row(self, level, origin, message):
        time = utc_now()
        cursor = self.db.execute("INSERT INTO events(time,level,origin,message) VALUES(?,?,?,?)",
                                 (time, level, origin, message))
        self.db.execute("DELETE FROM events WHERE id < (SELECT COALESCE(MAX(id),0)-1999 FROM events)")
        return {"id": cursor.lastrowid, "time": time, "level": level, "origin": origin,
                "message": message, "simulated": True}

    def _text_event(self, event):
        clean = lambda value: str(value).replace("\r", " ").replace("\n", " ").replace("\t", " ")
        try:
            with self.log_path.open("a", encoding="utf-8") as file:
                file.write("\t".join(clean(event[k]) for k in ("time", "level", "origin", "message")) + "\n")
            self.log_error = False
        except OSError:
            # Configuration/event transaction already committed. Do not report a
            # failed save when only the secondary text log failed.
            self.log_error = True
            print("OPC Bridge: arquivo texto de eventos indisponível; eventos preservados no banco.", file=sys.stderr)

    def event(self, level, origin, message):
        with self.lock:
            event = self._event_row(level, origin, message)
            self.db.commit()
            self._text_event(event)
            return event

    def mutate(self, operation, kind, data, expected_revision, identifier=None):
        with self.lock:
            try:
                self.db.execute("BEGIN IMMEDIATE")
                current = self.revision()
                if expected_revision != current:
                    raise ManagementError("A configuração foi alterada em outra janela. Atualize e tente novamente.", 409, "revision_conflict", current)
                old = None
                if operation != "create":
                    old = self._node(kind, identifier)
                if operation == "delete":
                    name = _strict_json(old["payload"])["name"]
                    self.db.execute("DELETE FROM nodes WHERE id=?", (identifier,))
                else:
                    if not isinstance(data, dict):
                        raise ManagementError("O corpo deve ser um objeto JSON.")
                    data = dict(data)
                    provided_parent = "parent_id" in data
                    parent_id = data.pop("parent_id", "root" if kind == "connections" else None)
                    if operation == "update":
                        if provided_parent and parent_id != old["parent_id"]:
                            if kind != "variables":
                                raise ManagementError("Somente variáveis podem ser movidas dentro da mesma máquina.")
                            old_machine = self._machine_for_folder(old["parent_id"])
                            new_machine = self._machine_for_folder(parent_id)
                            if old_machine != new_machine:
                                raise ManagementError("Uma variável não pode ser movida para outra máquina de destino.")
                        else:
                            parent_id = old["parent_id"]
                        payload = _strict_json(old["payload"])
                        payload.update(data)
                    else:
                        payload = data
                    payload = self._validate(kind, payload, parent_id, identifier)
                    name = payload["name"]
                    machine = self._machine_for_folder(parent_id) if kind == "variables" else None
                    encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False)
                    if operation == "create":
                        identifier = str(uuid.uuid4())
                        self.db.execute("INSERT INTO nodes VALUES(?,?,?,?,?,?,?)", (identifier, kind, parent_id, name.casefold(), encoded, machine, payload.get("item_id")))
                    else:
                        self.db.execute("UPDATE nodes SET parent_id=?,name_key=?,payload=?,scope_machine=?,item_id=? WHERE id=?", (parent_id, name.casefold(), encoded, machine, payload.get("item_id"), identifier))
                self.db.execute("UPDATE metadata SET value=? WHERE key='revision'", (str(current + 1),))
                verb = {"create": "criado", "update": "alterado", "delete": "excluído"}[operation]
                event = self._event_row("success", name, "Elemento %s; configuração salva somente no gerenciamento. Aplicação Windows pendente." % verb)
                self.db.commit()
            except Exception:
                self.db.rollback()
                raise
            self._text_event(event)
            return {"state": self.state(), "saved": True, "applied_to_windows": False}

    def variables(self, identifiers):
        with self.lock:
            return [dict(id=identifier, **_strict_json(self._node("variables", identifier)["payload"])) for identifier in identifiers]

    def variable_ids(self):
        with self.lock:
            return {row[0] for row in self.db.execute("SELECT id FROM nodes WHERE kind='variables'")}

    def events(self, after=0, limit=100):
        with self.lock:
            # Initial request returns the latest events, ordered oldest to newest.
            if after:
                rows = self.db.execute("SELECT * FROM events WHERE id>? ORDER BY id LIMIT ?", (after, limit)).fetchall()
            else:
                rows = list(reversed(self.db.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()))
            return {"events": [dict(row, simulated=True) for row in rows],
                    "last_id": rows[-1]["id"] if rows else after}
