"""Explicitly provisioned, local Windows OPC DA discovery/read over pinned SSH.

No network operation is permitted by an IP alone. Sources are operator-owned
provisioning, separate from destination agents. No automatic production discovery.
"""
from __future__ import annotations

import base64
import copy
import datetime as dt
import json
import math
import os
import re
import subprocess
import threading
import time
import uuid
from pathlib import Path

from .deployment import AgentError, RuntimeNotRunning, SshLabChannel, encoded
from .store import ManagementError, _valid_host, utc_now


class SourceError(AgentError):
    def __init__(self, message, hresult=-2147467259, transport=None, error_code="source_error"):
        super().__init__(message)
        self.hresult = hresult if isinstance(hresult, int) and -2147483648 <= hresult < 0 else -2147467259
        self.transport = transport
        self.error_code = error_code if isinstance(error_code, str) and re.fullmatch(r"[a-z0-9_]{1,80}", error_code) else "source_error"


class SshSourceChannel(SshLabChannel):
    def request(self, target, request):
        request = dict(request)
        expected_transport = target.get("read_transport") if request.get("operation") == "read" else None
        if expected_transport:
            request["transport"] = expected_transport
        script = target["agent_dir"] + "\\opc_da_source_agent.ps1"
        payload = base64.b64encode(encoded(request).encode("utf-8")).decode()
        command = "& '" + script + "' -RequestBase64 '" + payload + "'"
        program = target.get("powershell_executable", "powershell.exe")
        if program != "powershell.exe" and not re.fullmatch(r"[A-Za-z]:\\Windows\\(?:System32|SysWOW64)\\WindowsPowerShell\\v1\.0\\powershell\.exe", program, re.I):
            raise AgentError("Executável PowerShell da origem não permitido no provisionamento.")
        remote = program + " -STA -NoProfile -ExecutionPolicy Bypass -EncodedCommand " + base64.b64encode(command.encode("utf-16le")).decode()
        if len(remote) > 30000:
            raise AgentError("Pedido excede o limite de comando Windows; divida os itens em módulos menores.")
        if expected_transport not in (None, "opc_da_automation", "rust_da_com_fixture", "rust_lab_loopback_snapshot"):
            raise AgentError("Transporte de leitura não permitido no provisionamento.")
        timeout = 60 if expected_transport == "rust_da_com_fixture" else 20
        try:
            result = subprocess.run(["ssh"] + self.options(target) + [target["username"] + "@" + target["host"], remote],
                                    capture_output=True, timeout=timeout)
            if len(result.stdout) > 2_097_152:
                raise ValueError()
            reply = json.loads(result.stdout.decode("utf-8-sig"))
        except (OSError, ValueError, subprocess.TimeoutExpired):
            raise AgentError("Agente de origem indisponível ou resposta inválida; limite de consulta de %s segundos." % timeout)
        if result.returncode or reply.get("ok") is not True:
            # Remote exceptions may contain credentials/paths; expose only fixed codes.
            codes = {"source_error", "com_timeout", "worker_failed", "identity_mismatch", "progid_not_authorized", "progid_not_registered", "clsid_mismatch", "request_too_large", "operation_not_allowed", "invalid_item_count", "invalid_item_id", "invalid_timestamp_policy", "lab_snapshot_disabled", "lab_snapshot_unavailable", "lab_snapshot_identity_mismatch", "lab_snapshot_item_invalid", "lab_snapshot_config_invalid", "source_fixture_not_authorized", "source_fixture_unavailable", "source_fixture_hash_mismatch", "source_fixture_catalog_mismatch", "source_fixture_read_failed"}
            code = reply.get("error_code", "source_error")
            if not isinstance(code, str) or code not in codes: code = "source_error"
            phase = reply.get("diagnostic_stage", "")
            if not isinstance(phase, str) or not re.fullmatch(r"[A-Za-z0-9_]{1,80}", phase): phase = "não informada"
            error = SourceError("", reply.get("hresult"))
            raise SourceError("Agente de origem recusou a consulta (%s); etapa %s; HRESULT 0x%08X." % (code, phase, error.hresult & 0xffffffff), error.hresult, expected_transport, code)
        if expected_transport and reply.get("transport") != expected_transport:
            raise SourceError("O agente respondeu por um transporte diferente do provisionado.", -2147467259, expected_transport, "transport_mismatch")
        if expected_transport == "rust_lab_loopback_snapshot":
            if (reply.get("read_method") != "GET /v1/snapshot over SSH-local loopback"
                    or not isinstance(reply.get("source_pid"), int) or reply["source_pid"] <= 0
                    or not isinstance(reply.get("source_session"), int) or reply["source_session"] < 0):
                raise SourceError("Snapshot do laboratório sem identidade de processo válida.", -2147467259, expected_transport, "lab_snapshot_identity_mismatch")
        return reply


class Sources:
    LAB_SNAPSHOT_PROGID = "RustDaBridge.Lab.1"
    LAB_SNAPSHOT_CLSID = "{7A2F45B1-64D3-4E8C-9A11-3C852B7F2140}"
    LAB_SNAPSHOT_HOST = "10.247.140.166"
    LAB_SNAPSHOT_HOSTNAME = "RB1PI2"
    LAB_SNAPSHOT_AGENT_DIR = r"D:\OPCB\rust-da-source-agent-20261106-discovery-build4"
    LAB_SNAPSHOT_ITEMS = {
        "RB1PI1.TESTE.TESTE1.TESTE2": "Double",
        "RB1PI1.TESTE.TESTE1.DIGITAL": "Boolean",
    }
    OPC_VARTYPES = {"Boolean": 11, "Double": 5, "Float": 4}

    def __init__(self, store, deployments, channel=None):
        self.store, self.deployments = store, deployments
        self.channel = channel or SshSourceChannel(store.directory)
        self.lock = threading.RLock()
        self.relay_lock = threading.RLock()
        self.path = store.directory / "source-discovery.json"
        self.catalog_error = False
        try:
            self.catalogs = json.loads(self.path.read_text()) if self.path.exists() else {}
            if not isinstance(self.catalogs, dict): raise ValueError()
        except (OSError, ValueError):
            self.catalogs = {}; self.catalog_error = True
            store.event("warning", "Origem OPC DA", "Catálogo de descoberta inválido; arquivo preservado. Restaure um backup antes de buscar/aplicar origens.")
        self.states = {}
        self.stop = threading.Event()
        self.thread = None
        self.sessions = {}
        self.next_poll = {}
        self.failures = {}
        self.dashboard_config_path = store.directory / "lab-dashboard-snapshot.json"
        self.dashboard_cache_path = store.directory / "lab-dashboard-snapshot-cache.json"
        self.dashboard_profile = None
        self.dashboard_state = {"status": "disabled", "message": "Snapshot de laboratório não configurado.",
                                "checked_at": None, "source_pid": None, "source_session": None,
                                "samples": {}}
        self.dashboard_next_poll = 0.0
        self.dashboard_failures = 0
        self.dashboard_lock = threading.RLock()
        try:
            if self.dashboard_cache_path.exists():
                cached = json.loads(self.dashboard_cache_path.read_text(encoding="utf-8"))
                self._validate_dashboard_profile(cached.get("profile"))
                self.dashboard_profile = cached["profile"]
                self.dashboard_state = {"status": "unavailable",
                    "message": "Aguardando uma nova confirmação do snapshot após iniciar o configurador.",
                    "checked_at": cached.get("checked_at"), "source_pid": None,
                    "source_session": None, "samples": {}}
                for binding in self.dashboard_profile["bindings"]:
                    old = cached.get("samples", {}).get(binding["variable_id"], {})
                    self.dashboard_state["samples"][binding["variable_id"]] = self._bad_dashboard_sample(
                        binding, self.dashboard_state["message"], old.get("source_timestamp"))
        except (OSError, ValueError, TypeError, KeyError, ManagementError):
            # A malformed cache is never treated as a current or Good sample.
            self.dashboard_state = {"status": "unavailable",
                "message": "Cache de snapshot inválido; nenhuma amostra antiga será apresentada como atual.",
                "checked_at": utc_now(), "source_pid": None, "source_session": None, "samples": {}}

    def provision(self, host, transport=None, prog_id=None):
        path = self.store.directory / "source-agents.json"
        data = json.loads(path.read_text()) if path.exists() else {}
        if data.get("schema_version", 1) != 1 or not isinstance(data.get("sources", {}), dict):
            raise AgentError("Provisionamento de origem inválido; arquivo preservado.")
        sources = data["sources"]
        candidates = []
        direct = sources.get(host)
        if isinstance(direct, dict):
            candidates.append(direct)
        # Keep one host address capable of carrying separate read profiles.
        # Profile keys are operator-owned aliases; the SSH host remains pinned
        # and must still match the configured address and identity.
        candidates.extend(value for key, value in sources.items()
                          if key != host and isinstance(value, dict) and value.get("host") == host)
        candidates = [target for target in candidates
                      if target.get("enabled") is True and target.get("host") == host]
        if transport is not None:
            candidates = [target for target in candidates
                          if target.get("read_transport", "opc_da_automation") == transport]
        if prog_id is not None:
            candidates = [target for target in candidates
                          if "allowed_progids" not in target or
                          (isinstance(target.get("allowed_progids"), list) and
                           prog_id in target.get("allowed_progids", []))]
        if len(candidates) != 1:
            if not candidates:
                raise AgentError("Origem não provisionada/autorizada para este transporte.")
            raise AgentError("Há mais de um perfil de origem compatível; selecione um provisionamento inequívoco.")
        target = candidates[0]
        if target.get("enabled") is not True or target.get("host") != host:
            raise AgentError("Origem não provisionada/autorizada. Instale e vincule explicitamente o agente antes da busca.")
        return target

    @staticmethod
    def verify(target, reply):
        identity = reply.get("identity", {})
        if (identity.get("hostname", "").casefold() != target["expected_hostname"].casefold()
                or (target.get("expected_process_architecture") is not None and identity.get("process_architecture") != target["expected_process_architecture"])
                or target.get("expected_address", target["host"]) not in identity.get("addresses", [])):
            raise AgentError("Identidade do agente de origem divergente; nenhuma lista/amostra foi aceita.")

    def discover(self, host):
        if self.catalog_error: raise ManagementError("Catálogo de descoberta inválido; restaure um backup antes da busca.", 503, "source_catalog_invalid")
        if not isinstance(host, str) or not _valid_host(host):
            raise ManagementError("Informe um IP/hostname de origem válido.")
        try:
            # Prefer the per-user direct COM reader for this lab fixture. Other
            # explicitly provisioned sources continue to use OPC Automation.
            try:
                target = self.provision(host, "rust_da_com_fixture")
            except AgentError:
                target = self.provision(host, "opc_da_automation")
            reply = self.channel.request(target, {"operation": "discover"})
            self.verify(target, reply)
            servers = reply.get("servers")
            if not isinstance(servers, list) or len(servers) > 4096:
                raise AgentError("Lista de servidores inválida.")
            for server in servers:
                if (not isinstance(server, dict) or not isinstance(server.get("prog_id"), str)
                        or not re.fullmatch(r"[A-Za-z0-9_. -]{1,200}", server["prog_id"])
                        or not re.fullmatch(r"\{[0-9A-Fa-f-]{36}\}", server.get("clsid", ""))):
                    raise AgentError("Candidato OPC DA inválido.")
            record = {"discovery_id": str(uuid.uuid4()), "host": host, "identity": reply["identity"],
                      "transport": target["read_transport"],
                      "servers": servers, "discovered_at": utc_now(), "availability_tested": False}
            with self.lock:
                self.catalogs[record["discovery_id"]] = record
                # Bound persisted discovery records while keeping configured selections.
                selected = {m.get("source_discovery_id") for c in self.store.state(False)["connections"] for d in c["machines"] for m in d["modules"]}
                for key in list(self.catalogs)[:-128]:
                    if key not in selected: self.catalogs.pop(key)
                temp = self.path.with_suffix(".tmp")
                temp.write_text(encoded(self.catalogs), encoding="utf-8"); temp.chmod(0o600); temp.replace(self.path)
            return copy.deepcopy(record)
        except (AgentError, OSError, ValueError) as error:
            raise ManagementError(str(error) if isinstance(error, AgentError) else "Provisionamento de origem inválido.", 503, "source_unavailable")

    def validate_selection(self, module):
        if module.get("source_kind", "simulated") != "opc_da": return
        with self.lock:
            record = self.catalogs.get(module.get("source_discovery_id"))
        if (not record or record["host"] != module.get("source_host")
                or not any(s["prog_id"] == module.get("source_progid") and s["clsid"] == module.get("source_clsid") for s in record["servers"])):
            raise ManagementError("Busque e selecione um servidor OPC DA da origem informada antes de salvar.", 409, "source_selection_required")
        try: self.provision(record["host"], record.get("transport", "opc_da_automation"), module.get("source_progid"))
        except (AgentError, OSError, ValueError):
            raise ManagementError("Agente de origem não está provisionado/autorizado.", 409, "source_unconfigured")

    def status(self, machine_id):
        with self.lock: origins = copy.deepcopy(self.states.get(machine_id, {}))
        state, machine = self.deployments.machine(machine_id)
        views = [dict(origins.get(m["id"], {"status": "idle", "message": "Coleta ainda não confirmada."}), module_id=m["id"])
                 for m in machine["modules"] if m.get("source_kind") == "opc_da"]
        priority = {"connected": 0, "idle": 1, "waiting": 1, "degraded": 2, "unavailable": 3}
        status = max((v["status"] for v in views), key=lambda s: priority[s], default="idle")
        return {"status": status, "message": " · ".join(v["message"] for v in views) or "Nenhuma origem OPC DA configurada.", "origins": views}

    def _validate_dashboard_profile(self, profile):
        if not isinstance(profile, dict) or profile.get("schema_version") != 1 or profile.get("enabled") is not True:
            raise ManagementError("Perfil de snapshot de laboratório inválido ou desabilitado.")
        if (not isinstance(profile.get("profile_id"), str)
                or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", profile["profile_id"])
                or not isinstance(profile.get("machine_id"), str)
                or not isinstance(profile.get("source_host"), str)
                or not _valid_host(profile["source_host"])
                or profile["source_host"] != self.LAB_SNAPSHOT_HOST
                or profile.get("prog_id") != self.LAB_SNAPSHOT_PROGID
                or profile.get("clsid", "").casefold() != self.LAB_SNAPSHOT_CLSID.casefold()):
            raise ManagementError("Identidade do perfil de snapshot não autorizada.")
        interval = profile.get("interval_s")
        if isinstance(interval, bool) or not isinstance(interval, int) or not 5 <= interval <= 3600:
            raise ManagementError("Intervalo do snapshot deve ser de 5 a 3600 segundos.")
        bindings = profile.get("bindings")
        if not isinstance(bindings, list) or not 1 <= len(bindings) <= len(self.LAB_SNAPSHOT_ITEMS):
            raise ManagementError("O perfil de laboratório aceita apenas as duas tags autorizadas.")
        seen_variables, seen_items, seen_sources = set(), set(), set()
        for binding in bindings:
            if not isinstance(binding, dict):
                raise ManagementError("Vínculo de snapshot inválido.")
            variable_id = binding.get("variable_id")
            published_id = binding.get("published_item_id")
            source_id = binding.get("source_item_id")
            data_type = binding.get("data_type")
            if (not isinstance(variable_id, str) or not re.fullmatch(r"[0-9a-f-]{36}", variable_id, re.I)
                    or published_id not in self.LAB_SNAPSHOT_ITEMS
                    or source_id not in self.LAB_SNAPSHOT_ITEMS
                    or self.LAB_SNAPSHOT_ITEMS[source_id] != data_type
                    or data_type != self.LAB_SNAPSHOT_ITEMS[published_id]
                    or source_id != published_id
                    or variable_id in seen_variables or published_id in seen_items or source_id in seen_sources):
                raise ManagementError("Item ID, tipo ou associação do snapshot não autorizado.")
            seen_variables.add(variable_id); seen_items.add(published_id); seen_sources.add(source_id)
        return profile

    def _read_dashboard_profile(self):
        if not self.dashboard_config_path.exists():
            return None
        profile = json.loads(self.dashboard_config_path.read_text(encoding="utf-8"))
        self._validate_dashboard_profile(profile)
        state = self.store.state(False)
        machine = next((m for c in state["connections"] for m in c["machines"]
                        if m["id"] == profile["machine_id"]), None)
        if machine is None:
            raise ManagementError("Máquina do perfil de snapshot não existe na configuração persistente.")
        variables = {v["id"]: (m, v) for c in state["connections"] for m in c["machines"]
                     for module in m["modules"] for folder in module["folders"] for v in folder["variables"]}
        for binding in profile["bindings"]:
            entry = variables.get(binding["variable_id"])
            if (entry is None or entry[0]["id"] != profile["machine_id"]
                    or entry[1]["item_id"] != binding["published_item_id"]
                    or entry[1]["data_type"] != binding["data_type"]):
                raise ManagementError("Vínculo snapshot não corresponde à configuração persistente; Item IDs preservados.")
        target = self.provision(profile["source_host"], "rust_lab_loopback_snapshot")
        lab = target.get("lab_snapshot") or {}
        if (target.get("read_transport") != "rust_lab_loopback_snapshot"
                or lab.get("enabled") is not True
                or lab.get("prog_id") != profile["prog_id"]
                or lab.get("port") != 49321
                or str(lab.get("clsid", "")).casefold() != profile["clsid"].casefold()
                or target.get("expected_hostname", "").casefold() != self.LAB_SNAPSHOT_HOSTNAME.casefold()
                or target.get("expected_address") != self.LAB_SNAPSHOT_HOST
                or target.get("expected_process_architecture") != 32
                or target.get("agent_dir") != self.LAB_SNAPSHOT_AGENT_DIR):
            raise ManagementError("Agente provisionado não corresponde ao servidor Rust DA de laboratório autorizado.")
        allowed = {x.get("source_item_id"): x.get("data_type") for x in lab.get("items", []) if isinstance(x, dict)}
        if allowed != self.LAB_SNAPSHOT_ITEMS or not {b["source_item_id"] for b in profile["bindings"]}.issubset(allowed):
            raise ManagementError("Perfil inclui Item IDs ausentes da allowlist do agente.")
        self.dashboard_profile = profile
        return profile

    @staticmethod
    def _filetime_iso(value):
        if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= 2650467743999999999:
            return None
        try:
            return (dt.datetime(1601, 1, 1, tzinfo=dt.timezone.utc)
                    + dt.timedelta(microseconds=value // 10)).isoformat(timespec="microseconds").replace("+00:00", "Z")
        except (OverflowError, ValueError):
            return None

    @staticmethod
    def _bad_dashboard_sample(binding, message, last_timestamp=None):
        return {"variable_id": binding["variable_id"], "item_id": binding["published_item_id"],
                "source_id": binding["source_item_id"], "data_type": binding["data_type"],
                "value": None, "quality": "Bad", "quality_code": 8,
                "source_timestamp": None, "source_timestamp_filetime": None,
                "source_vartype": None, "source_kind": "simulated", "origin": "Snapshot de laboratório · Fonte simulada",
                "lab_snapshot": True, "snapshot_status": "unavailable", "snapshot_message": message,
                "snapshot_method": "GET /v1/snapshot", "snapshot_last_good_timestamp": last_timestamp,
                "applied_to_windows": False}

    def _invalid_profile_samples(self, variables):
        """Fail closed for this lab's IDs if a present sidecar is malformed on first start."""
        if not self.dashboard_config_path.exists():
            return {}
        try:
            raw = json.loads(self.dashboard_config_path.read_text(encoding="utf-8"))
            machine_id = raw.get("machine_id") if isinstance(raw, dict) else None
            state = self.store.state(False)
            machine = next((m for c in state["connections"] for m in c["machines"]
                            if m["id"] == machine_id and m["host"] == self.LAB_SNAPSHOT_HOST), None)
            if machine is None:
                return {}
        except (OSError, ValueError, TypeError, KeyError):
            return {}
        machine_variables = {v["id"] for module in machine["modules"] for folder in module["folders"]
                             for v in folder["variables"]}
        opc_da_variables = {v["id"] for module in machine["modules"] if module.get("source_kind") == "opc_da"
                            for folder in module["folders"] for v in folder["variables"]}
        output = {}
        for variable in variables:
            expected_type = self.LAB_SNAPSHOT_ITEMS.get(variable.get("item_id"))
            if (variable.get("id") in opc_da_variables or variable.get("id") not in machine_variables or expected_type is None
                    or variable.get("data_type") != expected_type):
                continue
            binding = {"variable_id": variable["id"], "published_item_id": variable["item_id"],
                       "source_item_id": variable["item_id"], "data_type": expected_type}
            sample = self._bad_dashboard_sample(binding,
                "Perfil persistente de snapshot inválido; nenhum cache anterior será apresentado como atual.")
            sample["snapshot_status"] = "invalid"
            output[variable["id"]] = sample
        return output

    def _persist_dashboard_state(self, profile):
        payload = {"schema_version": 1, "profile": profile, "status": self.dashboard_state["status"],
                   "message": self.dashboard_state["message"], "checked_at": self.dashboard_state["checked_at"],
                   "source_pid": self.dashboard_state["source_pid"],
                   "source_session": self.dashboard_state["source_session"],
                   "samples": {key: {k: value for k, value in sample.items()
                                     if k in ("source_timestamp", "source_timestamp_filetime", "value")}
                               for key, sample in self.dashboard_state["samples"].items()}}
        temp = self.dashboard_cache_path.with_suffix(".tmp")
        temp.write_text(encoded(payload), encoding="utf-8")
        temp.chmod(0o600)
        os.replace(temp, self.dashboard_cache_path)

    def _fail_dashboard_snapshot(self, profile, code):
        prior = self.dashboard_state.get("samples", {})
        samples = {b["variable_id"]: self._bad_dashboard_sample(
            b, "Snapshot indisponível; nenhuma amostra antiga é atual e Good.",
            prior.get(b["variable_id"], {}).get("source_timestamp") or
            prior.get(b["variable_id"], {}).get("snapshot_last_good_timestamp")) for b in profile["bindings"]}
        message = "Snapshot indisponível (%s); valores atuais foram limpos e qualidade rebaixada." % code
        with self.dashboard_lock:
            self.dashboard_state = {"status": "unavailable", "message": message, "checked_at": utc_now(),
                "source_pid": None, "source_session": None, "samples": samples}
            try: self._persist_dashboard_state(profile)
            except OSError: pass
        self.dashboard_failures = min(4, self.dashboard_failures + 1)
        if self.dashboard_failures == 1 or self.dashboard_failures % 4 == 0:
            self.store.event("warning", "Snapshot de laboratório", message)

    def _dashboard_stale(self, profile, state):
        checked = state.get("checked_at")
        if not checked:
            return True
        try:
            timestamp = dt.datetime.fromisoformat(checked.replace("Z", "+00:00"))
            age = (dt.datetime.now(dt.timezone.utc) - timestamp.astimezone(dt.timezone.utc)).total_seconds()
        except (AttributeError, TypeError, ValueError):
            return True
        return age > max(15, profile["interval_s"] * 3)

    def _poll_dashboard_snapshot(self):
        profile = None
        try:
            profile = self._read_dashboard_profile()
            if profile is None:
                if self.dashboard_profile is not None:
                    self._fail_dashboard_snapshot(self.dashboard_profile, "profile_missing")
                else:
                    with self.dashboard_lock:
                        self.dashboard_state = {"status": "disabled", "message": "Snapshot de laboratório não configurado.",
                                                "checked_at": None, "source_pid": None, "source_session": None, "samples": {}}
                return
            state = self.store.state(False)
            real_ids = {v["id"] for c in state["connections"] for m in c["machines"]
                        for module in m["modules"] if module.get("source_kind") == "opc_da"
                        for folder in module["folders"] for v in folder["variables"]}
            if all(b["variable_id"] in real_ids for b in profile["bindings"]):
                # Keep persisted monitor IDs, but never delay real collection by
                # querying a legacy simulated overlay that no longer supplies them.
                with self.dashboard_lock:
                    self.dashboard_profile = profile
                    self.dashboard_state = {"status": "superseded", "message": "Monitoramento atendido pela origem OPC DA configurada; snapshot simulado não consultado.",
                        "checked_at": utc_now(), "source_pid": None, "source_session": None, "samples": {}}
                return
            target = self.provision(profile["source_host"], "rust_lab_loopback_snapshot")
            request = {"operation": "read", "prog_id": profile["prog_id"], "clsid": profile["clsid"],
                       "items": [{"source_item_id": b["source_item_id"], "item_id": b["published_item_id"],
                                  "data_type": b["data_type"]} for b in profile["bindings"]]}
            reply = self.channel.request(target, request)
            self.verify(target, reply)
            if (reply.get("transport") != "rust_lab_loopback_snapshot"
                    or reply.get("read_method") != "GET /v1/snapshot over SSH-local loopback"
                    or not isinstance(reply.get("source_pid"), int) or reply["source_pid"] <= 0
                    or not isinstance(reply.get("source_session"), int) or reply["source_session"] < 0):
                raise SourceError("Snapshot sem identidade de processo válida.", transport="rust_lab_loopback_snapshot", error_code="lab_snapshot_identity_mismatch")
            raw = reply.get("samples")
            expected = {(b["published_item_id"], b["source_item_id"]): b for b in profile["bindings"]}
            if not isinstance(raw, list) or len(raw) != len(expected) or {(s.get("item_id"), s.get("source_item_id")) for s in raw if isinstance(s, dict)} != set(expected):
                raise SourceError("Snapshot retornou um catálogo divergente.", transport="rust_lab_loopback_snapshot", error_code="lab_snapshot_item_invalid")
            samples = {}
            degraded = False
            for item in raw:
                binding = expected[(item["item_id"], item["source_item_id"])]
                data_type = binding["data_type"]
                value = item.get("value")
                valid_value = (isinstance(value, bool) if data_type == "Boolean" else
                               isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value))
                expected_vartype = 11 if data_type == "Boolean" else 5
                quality = item.get("quality")
                if isinstance(quality, bool) or not isinstance(quality, int) or not 0 <= quality <= 65535:
                    raise SourceError("Qualidade do snapshot inválida.", transport="rust_lab_loopback_snapshot", error_code="lab_snapshot_item_invalid")
                timestamp_ticks = item.get("source_timestamp_filetime")
                timestamp = self._filetime_iso(timestamp_ticks)
                if not valid_value:
                    quality = 8; value = None; timestamp = None; timestamp_ticks = None; degraded = True
                elif quality & 0xC0 == 0xC0 and timestamp is None:
                    quality = (quality & ~0xC0) | 0x40
                    degraded = True
                if quality & 0xC0 != 0xC0:
                    value = None; timestamp = None; timestamp_ticks = None; degraded = True
                if item.get("source_vartype") != expected_vartype:
                    raise SourceError("Tipo declarado no snapshot diverge do vínculo.", transport="rust_lab_loopback_snapshot", error_code="lab_snapshot_item_invalid")
                samples[binding["variable_id"]] = {
                    "variable_id": binding["variable_id"], "item_id": binding["published_item_id"],
                    "source_id": binding["source_item_id"], "data_type": data_type, "value": value,
                    "quality": "Good" if quality & 0xC0 == 0xC0 else "Uncertain" if quality & 0xC0 == 0x40 else "Bad",
                    "quality_code": quality, "source_timestamp": timestamp,
                    "source_timestamp_filetime": timestamp_ticks, "source_vartype": expected_vartype,
                    "type_evidence": "rust_runtime_snapshot_declared_type", "source_kind": "simulated",
                    "origin": "Snapshot de laboratório · Fonte simulada", "lab_snapshot": True,
                    "snapshot_status": "degraded" if degraded else "connected", "snapshot_message": "Cache simulado confirmado no servidor Rust DA de laboratório.",
                    "snapshot_method": "GET /v1/snapshot", "applied_to_windows": False}
            for sample in samples.values():
                sample["snapshot_status"] = "degraded" if degraded else "connected"
            with self.dashboard_lock:
                self.dashboard_state = {"status": "degraded" if degraded else "connected",
                    "message": "Cache simulado confirmado no servidor Rust DA de laboratório." if not degraded else "Snapshot disponível com qualidade ou timestamp degradado.",
                    "checked_at": utc_now(), "source_pid": reply["source_pid"],
                    "source_session": reply["source_session"], "samples": samples}
                self._persist_dashboard_state(profile)
            self.dashboard_failures = 0
        except Exception as failure:
            if profile is None:
                try: profile = self._read_dashboard_profile()
                except Exception: profile = self.dashboard_profile
            if profile is None:
                with self.dashboard_lock:
                    self.dashboard_state = {"status": "invalid", "message": "Perfil persistente de snapshot inválido; nenhuma amostra foi apresentada.",
                        "checked_at": utc_now(), "source_pid": None, "source_session": None, "samples": {}}
                self.dashboard_failures = min(4, self.dashboard_failures + 1)
                if self.dashboard_failures == 1 or self.dashboard_failures % 4 == 0:
                    self.store.event("error", "Snapshot de laboratório", "Perfil de snapshot inválido; verifique o arquivo privado de laboratório.")
                return
            code = failure.error_code if isinstance(failure, SourceError) else "snapshot_unavailable"
            self._fail_dashboard_snapshot(profile, code)

    def dashboard_samples_for(self, variables):
        state = self.store.state(False)
        opc_da_variables = {v["id"] for connection in state["connections"] for machine in connection["machines"]
                            for module in machine["modules"] if module.get("source_kind") == "opc_da"
                            for folder in module["folders"] for v in folder["variables"]}
        with self.dashboard_lock:
            profile = self.dashboard_profile
            if profile is None:
                try: profile = self._read_dashboard_profile()
                except Exception:
                    profile = self.dashboard_profile
                    if profile is None:
                        return self._invalid_profile_samples(variables)
            if profile is None: return {}
            bindings = {b["variable_id"]: b for b in profile["bindings"]}
            output = {}
            state = self.dashboard_state
            stale = self._dashboard_stale(profile, state) and state.get("status") in ("connected", "degraded")
            state_status = "unavailable" if stale else state.get("status", "unavailable")
            state_message = ("Snapshot expirado; aguardando nova confirmação. O valor antigo não é atual nem Good."
                             if stale else state.get("message", "Aguardando o primeiro snapshot."))
            for variable in variables:
                # The legacy dashboard overlay may still contain these same IDs.
                # Once a variable is configured as OPC DA, its actual relay sample
                # must flow through deployment.read and must never be shadowed by
                # the simulated snapshot cache.
                if variable["id"] in opc_da_variables:
                    continue
                binding = bindings.get(variable["id"])
                if binding is None: continue
                if (variable.get("item_id") != binding["published_item_id"]
                        or variable.get("data_type") != binding["data_type"]):
                    output[variable["id"]] = self._bad_dashboard_sample(binding, "Configuração do Item ID/tipo mudou; snapshot recusado.")
                    continue
                sample = None if stale else state.get("samples", {}).get(variable["id"])
                if sample is None:
                    old = state.get("samples", {}).get(variable["id"], {})
                    sample = self._bad_dashboard_sample(binding, state_message,
                        old.get("source_timestamp") or old.get("snapshot_last_good_timestamp"))
                output[variable["id"]] = {**sample, "snapshot_status": state_status,
                    "snapshot_message": state_message,
                    "source_pid": state.get("source_pid"), "source_session": state.get("source_session"),
                    "snapshot_checked_at": state.get("checked_at")}
            return output

    def dashboard_status(self):
        with self.dashboard_lock:
            profile = self.dashboard_profile
            if profile is None:
                try: profile = self._read_dashboard_profile()
                except Exception:
                    return {"enabled": False, "status": "invalid",
                            "message": "Perfil persistente de snapshot inválido; nenhum valor foi aceito."}
            if profile is None:
                return {"enabled": False, "status": "disabled", "message": "Snapshot de laboratório não configurado."}
            state = self.dashboard_state
            stale = self._dashboard_stale(profile, state) and state.get("status") in ("connected", "degraded")
            return {"enabled": True, "profile_id": profile["profile_id"], "machine_id": profile["machine_id"],
                    "source_host": profile["source_host"], "prog_id": profile["prog_id"],
                    "interval_s": profile["interval_s"],
                    "bindings": [{"variable_id": b["variable_id"], "published_item_id": b["published_item_id"],
                                  "source_item_id": b["source_item_id"], "data_type": b["data_type"]}
                                 for b in profile["bindings"]],
                    "status": "unavailable" if stale else state.get("status", "unavailable"),
                    "message": "Snapshot expirado; aguardando nova confirmação." if stale else state.get("message", "Aguardando o primeiro snapshot."),
                    "checked_at": state.get("checked_at"),
                    "source_pid": None if stale else state.get("source_pid"),
                    "source_session": None if stale else state.get("source_session")}

    def validate_destination(self, machine):
        for module in machine["modules"]:
            if module.get("source_kind") != "opc_da": continue
            self.validate_selection(module)
            count = sum(len(f["variables"]) for f in module["folders"])
            if not 1 <= count <= 128:
                raise ManagementError("Cada origem OPC DA deve conter entre 1 e 128 itens nesta etapa.")
            request = {"operation": "read", "prog_id": module["source_progid"], "clsid": module["source_clsid"],
                       "items": [self._source_item_request(v) for f in module["folders"] for v in f["variables"]]}
            if len(encoded(request).encode("utf-8")) > 7000:
                raise ManagementError("Solicitação de origem excede 7 KiB nesta versão; divida os itens em módulos menores.")

    def start(self):
        self.thread = threading.Thread(target=self._loop, name="opc-da-source-relay", daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread: self.thread.join(35)

    def _loop(self):
        while not self.stop.wait(0.5):
            try:
                state = self.store.state(False)
                for c in state["connections"]:
                    for machine in c["machines"]:
                        for module in machine["modules"]:
                            if module.get("source_kind") != "opc_da": continue
                            key = (machine["id"], module["id"])
                            if time.monotonic() < self.next_poll.get(key, 0): continue
                            started = time.monotonic()
                            self.next_poll[key] = started + module["update_interval_s"]
                            if self.deployments.status(machine)["status"] == "applied":
                                self.poll_once(machine, module, state)
                                with self.lock: status = self.states.get(machine["id"], {}).get(module["id"], {}).get("status")
                                self._schedule_poll(key, status, started, module["update_interval_s"])
                now = time.monotonic()
                if now >= self.dashboard_next_poll:
                    # Let the poller own profile validation and failure handling. Reading
                    # the file here first used to swallow malformed-profile errors in
                    # this outer loop, leaving an old Good sample visible indefinitely.
                    self._poll_dashboard_snapshot()
                    profile = self.dashboard_profile
                    status = self.dashboard_state.get("status")
                    if profile is None or status in ("disabled", "invalid"):
                        self.dashboard_next_poll = time.monotonic() + 2
                    else:
                        failed = status == "unavailable"
                        backoff = 2 ** self.dashboard_failures if failed else 1
                        self.dashboard_next_poll = time.monotonic() + min(
                            60, profile["interval_s"] * backoff)
            except Exception:
                # Do not leak raw subprocess/COM exceptions into logs/UI.
                pass

    def _schedule_poll(self, key, status, started, interval):
        now = time.monotonic()
        if status == "waiting":
            self.failures[key] = 0
            self.next_poll[key] = now + 1
        elif status == "unavailable":
            self.failures[key] = min(4, self.failures.get(key, 0) + 1)
            self.next_poll[key] = now + min(60, interval * 2 ** self.failures[key])
        else:
            self.failures[key] = 0
            # Interval between cycle starts, not a second pause after IO.
            # The single worker never overlaps requests when a cycle overruns.
            self.next_poll[key] = max(now, started + interval)

    @staticmethod
    def _source_item_request(variable):
        return {"source_item_id": variable["source_id"], "item_id": variable["item_id"],
                "data_type": variable["data_type"],
                "source_data_type": variable.get("source_data_type", variable["data_type"]),
                "source_conversion": variable.get("source_conversion")}

    def _validate_opc_samples(self, variables, samples, transport):
        expected = {(v["item_id"], v["source_id"]): v for v in variables}
        if not isinstance(samples, list) or len(samples) != len(expected) or {
                (s.get("item_id"), s.get("source_item_id")) for s in samples if isinstance(s, dict)
        } != set(expected):
            raise SourceError("Origem retornou um catálogo diferente do solicitado.", transport=transport,
                              error_code="source_catalog_mismatch")
        checked = []
        for raw in samples:
            variable = expected[(raw["item_id"], raw["source_item_id"])]
            target_type = variable["data_type"]
            source_type = variable.get("source_data_type", target_type)
            conversion = variable.get("source_conversion")
            expected_source_vt = self.OPC_VARTYPES.get(source_type)
            expected_published_vt = self.OPC_VARTYPES[target_type]
            if expected_source_vt is None:
                raise SourceError("Tipo de origem não permitido.", transport=transport, error_code="source_type_invalid")
            hresult = raw.get("hresult", 0)
            if isinstance(hresult, bool) or not isinstance(hresult, int) or not -2147483648 <= hresult <= 2147483647:
                raise SourceError("HRESULT por item inválido.", transport=transport, error_code="source_sample_invalid")
            if raw.get("data_type") != target_type:
                raise SourceError("Tipo publicado diverge do vínculo.", transport=transport, error_code="source_type_mismatch")
            if raw.get("source_data_type", source_type) != source_type or raw.get("source_conversion") != conversion:
                raise SourceError("Metadados de tipo/conversão divergem do vínculo.", transport=transport,
                                  error_code="source_conversion_mismatch")
            if hresult >= 0:
                if raw.get("source_vartype") != expected_source_vt or raw.get("published_vartype", expected_published_vt) != expected_published_vt:
                    raise SourceError("VARTYPE retornado diverge do tipo de origem/publicação.", transport=transport,
                                      error_code="source_vartype_mismatch")
                value = raw.get("value")
                valid_value = (isinstance(value, bool) if target_type == "Boolean" else
                               isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value))
                if not valid_value:
                    raise SourceError("Valor OPC DA inválido para o tipo publicado.", transport=transport,
                                      error_code="source_value_invalid")
                if conversion == "float_to_double":
                    if source_type != "Float" or target_type != "Double":
                        raise SourceError("Conversão Float para Double não corresponde à configuração.", transport=transport,
                                          error_code="source_conversion_mismatch")
                    raw["value"] = float(value)
            # The deployed SourceBinding encodes source_data_type only for an
            # explicit conversion. For same-type sources the target runtime
            # uses None to mean "same as published type" and rejects a batch
            # that redundantly sends (for example) "Boolean". Preserve the
            # source VARIANT evidence above, but align these optional metadata
            # fields with the exact binding serialized by export_project.
            raw["source_data_type"] = source_type if conversion else None
            raw["source_conversion"] = conversion
            quality = raw.get("quality")
            if isinstance(quality, bool) or not isinstance(quality, int) or not 0 <= quality <= 65535:
                raise SourceError("Qualidade OPC DA inválida.", transport=transport, error_code="source_sample_invalid")
            ticks = raw.get("source_timestamp_filetime")
            if ticks is not None and (isinstance(ticks, bool) or not isinstance(ticks, int) or not 0 < ticks <= 2650467743999999999):
                raise SourceError("Timestamp de origem inválido.", transport=transport, error_code="source_timestamp_invalid")
            checked.append(raw)
        return checked

    def poll_once(self, machine, module, state):
        with self.relay_lock: self._poll_once(machine, module, state)

    def _poll_once(self, machine, module, state):
        mid = machine["id"]
        started = time.monotonic()
        timings = {}
        transport = None
        target = None
        try:
            self.validate_selection(module)
            target = self.provision(module["source_host"], prog_id=module["source_progid"])
            transport = target.get("read_transport", "opc_da_automation")
            destination = self.deployments.target(machine)
            receipt = self.deployments.status(machine).get("receipt", {})
            # Receipt may refer to a still-valid older global revision for an unchanged machine.
            revision, digest = receipt.get("configuration_revision"), receipt.get("project_sha256")
            header = {"machine_id": mid, "configuration_revision": revision, "config_sha256": digest,
                      "source_module_id": module["id"]}
            key = (mid, module["id"], revision, digest)
            session = self.sessions.get(key)
            if session is None:
                session_started = time.monotonic()
                ack = self.deployments.channel.runtime(destination, {**header, "operation": "source-session"})
                timings["session"] = round((time.monotonic() - session_started) * 1000, 1)
                if any(ack.get(k) != v for k, v in header.items()): raise AgentError("Sessão de destino divergente.")
                self.sessions = {k: v for k, v in self.sessions.items() if k[:2] != key[:2]}
                session = self.sessions[key] = [ack["session_id"], 0]
            if self.stop.is_set(): return
            variables = [v for f in module["folders"] for v in f["variables"]]
            request = {"operation": "read", "prog_id": module["source_progid"], "clsid": module["source_clsid"],
                       "items": [self._source_item_request(v) for v in variables]}
            error = None
            read_started = time.monotonic()
            try:
                reply = self.channel.request(target, request); self.verify(target, reply)
                if target.get("read_transport") and reply.get("transport") != target["read_transport"]:
                    raise SourceError("O agente respondeu por um transporte diferente do provisionado.", -2147467259, target["read_transport"], "transport_mismatch")
                transport = reply.get("transport", transport)
                samples = self._validate_opc_samples(variables, reply.get("samples", []), transport)
            except (AgentError, OSError, ValueError) as failure:
                source_hresult = failure.hresult if isinstance(failure, SourceError) else -2147023174
                if isinstance(failure, SourceError):
                    error = "Origem indisponível (%s); último valor não é atual; HRESULT 0x%08X." % (failure.error_code, failure.hresult & 0xffffffff)
                else:
                    error = "Origem indisponível ou identidade/resposta inválida; último valor não é atual."
                samples = [{**item, "value": None, "quality": 8, "hresult": source_hresult,
                            "source_timestamp_filetime": None} for item in request["items"]]
            timings["source_read"] = round((time.monotonic() - read_started) * 1000, 1)
            if self.stop.is_set(): return
            session[1] += 1
            delivery_started = time.monotonic()
            ack = self.deployments.channel.runtime(destination, {**header, "operation": "source-samples", "session_id": session[0],
                                                               "sequence": session[1], "samples": samples})
            timings["delivery"] = round((time.monotonic() - delivery_started) * 1000, 1)
            timings["total"] = round((time.monotonic() - started) * 1000, 1)
            if ack.get("pid") is None or ack.get("sequence") != session[1] or any(ack.get(k) != v for k, v in header.items()):
                raise AgentError("Destino não confirmou a sequência de amostras.")
            bad = ack.get("all_good") is not True or any(s.get("hresult", 0) < 0 or s.get("quality", 0) & 192 != 192 for s in samples)
            status = "unavailable" if error else "degraded" if bad else "connected"
            if error:
                message = error
            elif bad:
                message = "Amostras com erro/qualidade degradada."
            elif transport == "rust_lab_loopback_snapshot":
                message = "Dados simulados confirmados pelo snapshot local do servidor Rust DA de laboratório; não é uma leitura COM OPC DA."
            else:
                message = "Leitura OPC DA confirmada pelo destino."
            self._state(mid, module["id"], status, message, ack, transport=transport,
                        source_environment=target.get("source_environment"),
                        read_method=(reply.get("read_method") or next(
                            (sample.get("read_method") for sample in samples if sample.get("read_method")), None)) if not error else None,
                        source_pid=reply.get("source_pid") if not error else None,
                        source_session=reply.get("source_session") if not error else None,
                        timings=timings)
        except Exception as failure:
            self.sessions = {k: v for k, v in self.sessions.items() if k[0] != mid}
            failure_transport = transport or getattr(failure, "transport", None)
            waiting = isinstance(failure, RuntimeNotRunning)
            self._state(mid, module["id"], "waiting" if waiting else "unavailable",
                        "Agente acessível; aguardando ativação do servidor OPC DA de destino." if waiting else "Coleta sem confirmação; o destino expira amostras automaticamente.", {}, transport=failure_transport,
                        source_environment=target.get("source_environment") if target else None)

    def _state(self, mid, module_id, status, message, ack, transport=None, source_environment=None,
               read_method=None, source_pid=None, source_session=None, timings=None):
        with self.lock:
            previous = self.states.get(mid, {}).get(module_id, {}).get("status")
            self.states.setdefault(mid, {})[module_id] = {"status": status, "message": message, "checked_at": utc_now(),
                "server_pid": ack.get("pid"), "transport": transport,
                "source_environment": source_environment, "read_method": read_method,
                "source_pid": source_pid, "source_session": source_session, "timings_ms": timings or {}}
        if previous != status:
            self.store.event("info" if status == "connected" else "warning", "Origem OPC DA", message)
