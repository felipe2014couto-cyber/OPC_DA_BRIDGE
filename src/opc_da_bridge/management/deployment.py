"""Selected-target laboratory deployment over pinned, key-authenticated SSH.

No remote command is accepted from an HTTP request. Target provisioning is an
operator-owned file outside Git; the only remote program is lab_agent.ps1.
"""
from __future__ import annotations
import base64
import copy
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path
from .store import ManagementError, utc_now


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def locate(state, machine_id):
    for connection in state["connections"]:
        for machine in connection["machines"]:
            if machine["id"] == machine_id:
                return connection, machine
    raise ManagementError("Máquina de destino não encontrada.", 404, "not_found")


def fingerprint(machine):
    return hashlib.sha256(encoded(machine).encode()).hexdigest()


def export_project(state, machine_id, target):
    connection, machine = locate(state, machine_id)
    modules = []
    for module in machine["modules"]:
        for folder in module["folders"]:
            if not folder["variables"]:
                continue
            variables = [{"name": v["name"], "item_id": v["item_id"],
                          "data_type": v["data_type"], "initial_value": v["initial_value"],
                          "description": v["description"], "node_id": v["source_id"]}
                         for v in folder["variables"]]
            if module.get("source_kind") == "opc_da":
                for source_variable, variable in zip(folder["variables"], variables):
                    variable["source"] = {"module_id": module["id"], "item_id": variable["node_id"],
                                          "stale_after_s": max(30, module["update_interval_s"] * 3)}
                    if "source_conversion" in source_variable:
                        variable["source"]["source_data_type"] = source_variable["source_data_type"]
                        variable["source"]["source_conversion"] = source_variable["source_conversion"]
            # Preserve names as metadata; published Item IDs are always explicit.
            modules.append({"name": module["name"] + "/" + folder["name"], "variables": variables})
    project = {"schema_version": 1, "name": machine["name"],
               "diagnostic_port": target.get("diagnostic_port", 49321),
               "deployment": {"machine_id": machine_id, "configuration_revision": state["revision"]},
               "connections": [{"name": connection["name"], "modules": modules}]}
    document = encoded(project)
    return {"schema_version": 1, "deployment_id": str(uuid.uuid4()), "machine_id": machine_id,
            "expected_hostname": target["expected_hostname"], "expected_address": target["host"],
            "configuration_revision": state["revision"], "project_json": document,
            "project_sha256": hashlib.sha256(document.encode("utf-8")).hexdigest()}


class AgentError(RuntimeError):
    pass


class RuntimeNotRunning(AgentError):
    """The authenticated agent is reachable, but its lab server is not active."""


class SshLabChannel:
    def __init__(self, directory):
        self.directory = Path(directory)

    @staticmethod
    def validate(target):
        for field in ("host", "username", "expected_hostname"):
            if not re.fullmatch(r"[A-Za-z0-9_.-]+", target.get(field, "")):
                raise AgentError("Provisionamento de identidade SSH inválido.")
        if not re.fullmatch(r"[A-Za-z]:\\[A-Za-z0-9_.\\-]+", target.get("agent_dir", "")) or ".." in target["agent_dir"]:
            raise AgentError("Diretório do agente não autorizado.")
        for key in ("identity_file", "known_hosts_file"):
            file = Path(target.get(key, ""))
            if not file.is_absolute() or not file.is_file():
                raise AgentError("Chave ou arquivo de confiança SSH indisponível.")
        if Path(target["identity_file"]).stat().st_mode & 0o077:
            raise AgentError("A chave SSH precisa de permissões privadas.")

    def options(self, target):
        self.validate(target)
        return ["-F", "/dev/null", "-i", target["identity_file"], "-o", "IdentitiesOnly=yes",
                "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o", "UpdateHostKeys=no",
                "-o", "UserKnownHostsFile=" + target["known_hosts_file"], "-o", "ConnectTimeout=5",
                "-o", "LogLevel=ERROR"]

    def invoke(self, target, operation, inbox=None, request_body=None):
        if operation not in ("identify", "snapshot", "apply", "telemetry"):
            raise AgentError("Operação de agente não permitida.")
        command = "& '" + target["agent_dir"] + "\\lab_agent.ps1' -Operation " + operation
        if inbox:
            if not re.fullmatch(r"[0-9a-f-]{36}\.json", inbox):
                raise AgentError("Documento de aplicação inválido.")
            command += " -InboxName '" + inbox + "'"
        if request_body is not None:
            if operation != "telemetry" or inbox is not None or len(request_body) > 2_097_152:
                raise AgentError("Pedido de telemetria via stdin inválido.")
            try:
                request_body.decode("ascii")
            except (UnicodeError, AttributeError):
                raise AgentError("Telemetria stdin exige JSON ASCII com escapes Unicode.")
            # PowerShell 2 consumes stdin into its pipeline before the script
            # runs. Forward it in memory; never interpolate payload into code.
            command = '$request=(@($input) -join "`n"); ' + command + " -RequestStdin -RequestJson $request"
        remote = "powershell.exe -NoProfile -ExecutionPolicy Bypass -EncodedCommand " + base64.b64encode(command.encode("utf-16le")).decode()
        try:
            kwargs = {"capture_output": True, "timeout": 90 if operation == "apply" else 12}
            if request_body is not None:
                kwargs["input"] = request_body
            result = subprocess.run(["ssh"] + self.options(target) + [target["username"] + "@" + target["host"], remote], **kwargs)
            data = json.loads(result.stdout.decode("utf-8-sig"))
        except (OSError, subprocess.TimeoutExpired, ValueError):
            raise AgentError("Não foi possível obter confirmação do agente pelo SSH verificado.")
        if result.returncode or data.get("ok") is not True:
            if data.get("error_code") == "runtime_not_running" or data.get("error") == "Laboratory runtime not available":
                raise RuntimeNotRunning("O agente está acessível; aguardando ativação do servidor OPC DA de laboratório.")
            message = str(data.get("error", "Agente recusou a operação."))[:300]
            raise AgentError(message.replace("\r", " ").replace("\n", " "))
        return data

    def identify(self, target):
        return self.invoke(target, "identify")

    def snapshot(self, target):
        return self.invoke(target, "snapshot")

    def runtime(self, target, payload):
        envelope = dict(payload, deployment_id=str(uuid.uuid4()), schema_version=1,
                        expected_hostname=target["expected_hostname"], expected_address=target["host"])
        transport = target.get("runtime_transport")
        if transport == "ssh_stdin_v1":
            body = json.dumps(envelope, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("ascii")
            return self.invoke(target, "telemetry", request_body=body)
        if transport is not None:
            raise AgentError("Transporte de telemetria não permitido.")
        return self._transfer(target, envelope, "telemetry")

    def apply(self, target, envelope):
        return self._transfer(target, envelope, "apply")

    def _transfer(self, target, envelope, operation):
        name = envelope["deployment_id"] + ".json"
        with tempfile.TemporaryDirectory(prefix="transfer-", dir=str(self.directory)) as directory:
            file = Path(directory) / name
            file.write_text(encoded(envelope), encoding="utf-8")
            os.chmod(str(file), 0o600)
            remote_path = target["agent_dir"].replace("\\", "/") + "/inbox/" + name
            try:
                result = subprocess.run(["scp"] + self.options(target) + [str(file), target["username"] + "@" + target["host"] + ":" + remote_path], capture_output=True, timeout=20)
            except (OSError, subprocess.TimeoutExpired):
                raise AgentError("Transferência SSH não concluída.")
            if result.returncode:
                raise AgentError("Transferência SSH recusada; confira a chave do host e o provisionamento.")
        return self.invoke(target, operation, name)


class Deployments:
    def __init__(self, store, channel=None):
        self.store = store
        self.channel = channel or SshLabChannel(store.directory)
        self.lock = threading.RLock()
        self.path = store.directory / "deployment-state.json"
        self.records = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        self.jobs = {}
        for record in self.records.values():
            if record.get("status") == "applying":
                record.update(status="pending", last_error="Aplicação interrompida; confirme novamente o destino.")

    def save(self):
        temp = self.path.with_name(self.path.name + ".tmp-" + uuid.uuid4().hex)
        try:
            with temp.open("x", encoding="utf-8") as file:
                os.chmod(str(temp), 0o600)
                file.write(encoded(self.records)); file.flush(); os.fsync(file.fileno())
            os.replace(str(temp), str(self.path))
        finally:
            if temp.exists(): temp.unlink()

    def targets(self):
        path = self.store.directory / "agents.json"
        if not path.exists(): return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("schema_version") != 1 or not isinstance(data.get("targets"), dict): raise ValueError()
            return data["targets"]
        except (ValueError, OSError):
            raise ManagementError("Arquivo de provisionamento do agente inválido; preservado.", 503, "agent_settings_invalid")

    def target(self, machine):
        target = self.targets().get(machine["id"])
        if not target: raise AgentError("Esta máquina ainda não tem um agente provisionado.")
        if target.get("host") != machine["host"]: raise AgentError("O endereço cadastrado difere do destino SSH provisionado.")
        return target

    @staticmethod
    def verify(machine, target, response):
        identity = response.get("identity", {})
        if response.get("machine_id") != machine["id"] or identity.get("hostname", "").casefold() != target["expected_hostname"].casefold() or machine["host"] not in identity.get("addresses", []):
            raise AgentError("A identidade confirmada não corresponde ao destino provisionado.")

    def status(self, machine):
        with self.lock:
            record = copy.deepcopy(self.records.get(machine["id"], {}))
        target = self.targets().get(machine["id"])
        result = {"status": "pending" if target else "unconfigured", "configured": bool(target),
                  "expected_hostname": target.get("expected_hostname") if target else None,
                  "last_error": None, **record}
        result["configured"] = bool(target)
        if result["status"] == "applied" and result.get("fingerprint") != fingerprint(machine):
            result["status"] = "pending"
        if target and target.get("host") != machine["host"]:
            result.update(status="failed", last_error="Endereço cadastrado difere do provisionamento.")
        return result

    def decorate(self, state):
        states = []
        for connection in state["connections"]:
            for machine in connection["machines"]:
                machine["deployment"] = self.status(machine)
                states.append(machine["deployment"]["status"])
        state["applied_to_windows"] = bool(states) and all(s == "applied" for s in states)
        state["deployment_status"] = "applied" if state["applied_to_windows"] else "pending"
        return state

    def machine(self, identifier):
        state = self.store.state(include_deployment=False)
        return state, locate(state, identifier)[1]



    def fail(self, identifier, message):
        with self.lock:
            self.records.setdefault(identifier, {}).update(status="failed", last_error=str(message)[:300], checked_at=utc_now())
            self.save()
        self.store.event("error", "Agente Windows", "Aplicação/verificação recusada: " + str(message)[:300])

    def indeterminate(self, identifier, message):
        with self.lock:
            self.records.setdefault(identifier, {}).update(status="indeterminate", last_error=str(message)[:300], checked_at=utc_now())
            self.save()
        self.store.event("error", "Agente Windows", "Resultado indeterminado após perda de comunicação: " + str(message)[:300])

    def start(self, identifier, revision):
        state, machine = self.machine(identifier)
        if revision != state["revision"]:
            raise ManagementError("Atualize a revisão antes de aplicar.", 409, "revision_conflict", state["revision"])
        try: target = self.target(machine)
        except AgentError as error: raise ManagementError(str(error), 409, "agent_unconfigured")
        envelope = export_project(state, identifier, target)
        with self.lock:
            if self.jobs.get(identifier) and self.jobs[identifier].is_alive():
                raise ManagementError("Já existe uma aplicação em andamento.", 409, "deployment_busy")
            self.records.setdefault(identifier, {}).update(status="applying", last_error=None, started_at=utc_now())
            self.save()
            worker = threading.Thread(target=self.apply, args=(machine, target, envelope), daemon=True)
            self.jobs[identifier] = worker; worker.start()
        return {"deployment": self.status(machine), "applied_to_windows": False}

    def _validate_confirmed_catalog(self, envelope, receipt, snapshot):
        identifier = envelope["machine_id"]
        project = json.loads(envelope["project_json"])
        variables = [v for c in project["connections"] for m in c["modules"] for v in m["variables"]]
        if (receipt.get("machine_id") != identifier or receipt.get("project_sha256") != envelope["project_sha256"]
                or str(receipt.get("configuration_revision")) != str(envelope["configuration_revision"])
                or receipt.get("variable_count") != len(variables)
                or snapshot.get("machine_id") != identifier or snapshot.get("config_sha256") != envelope["project_sha256"]
                or str(snapshot.get("configuration_revision")) != str(envelope["configuration_revision"])):
            raise AgentError("A confirmação do agente não corresponde à configuração enviada.")
        actual = {s["item_id"]: s for s in snapshot.get("samples", [])}
        if len(actual) != len(variables):
            raise AgentError("O catálogo confirmado difere dos itens enviados.")
        for v in variables:
            s = actual.get(v["item_id"])
            if not s or s["data_type"] != v["data_type"]:
                raise AgentError("Valores ou tipos do snapshot não correspondem à configuração.")
            if v.get("source"):
                continue  # Configuration ACK is not proof of a real sample.
            if s["value"] != v["initial_value"]:
                raise AgentError("Valor simulado não confirmado.")
            code = s.get("quality", 0)
            if (code & 192) != 192:
                raise AgentError("Qualidade não confirmada como Good.")
            if not s.get("source_timestamp_filetime"):
                raise AgentError("Timestamp do runtime ausente.")

    def _confirm_apply(self, machine, target, envelope, reply):
        identifier = machine["id"]
        receipt, snapshot = reply.get("receipt", {}), reply.get("snapshot", {})
        self._validate_confirmed_catalog(envelope, receipt, snapshot)
        with self.lock:
            self.records[identifier] = {"status": "applied", "fingerprint": fingerprint(machine),
                                        "receipt": receipt, "identity_hostname": reply["identity"]["hostname"],
                                        "confirmed_at": utc_now(), "last_error": None}
            self.save()
        self.store.event("success", machine["name"], "Configuração confirmada pelo agente Windows; fontes simuladas atualizadas.")

    def _is_communication_loss(self, error):
        msg = str(error).lower()
        return ("não foi possível obter confirmação" in msg
                or "transferência ssh" in msg
                or "timeout" in msg
                or "conexão" in msg
                or "connection" in msg
                or "lost" in msg
                or "unreachable" in msg
                or "indeterminado" in msg)

    def _handle_apply_error(self, machine, target, envelope, error):
        identifier = machine["id"]
        with self.lock:
            prev_record = self.records.get(identifier, {})
            prev_receipt = prev_record.get("receipt", {})
        # If failure occurred after sending envelope, check destination status
        try:
            reply = self.channel.identify(target)
            self.verify(machine, target, reply)
            receipt = reply.get("receipt", {})
            snapshot = reply.get("snapshot", {})
            # If destination committed candidate envelope:
            if (receipt.get("project_sha256") == envelope["project_sha256"]
                    and str(receipt.get("configuration_revision")) == str(envelope["configuration_revision"])
                    and receipt.get("machine_id") == identifier):
                self._validate_confirmed_catalog(envelope, receipt, snapshot)
                self._confirm_apply(machine, target, envelope, reply)
                self.store.event("warning", machine["name"], "Configuração confirmada após reconciliação por digest.")
                return
            # If destination definitely retained previous confirmed configuration:
            if (prev_receipt and receipt.get("project_sha256") == prev_receipt.get("project_sha256")
                    and str(receipt.get("configuration_revision")) == str(prev_receipt.get("configuration_revision"))):
                self.fail(identifier, str(error) if isinstance(error, AgentError) else "Aplicação não confirmada pelo destino; configuração anterior preservada.")
                return
        except Exception:
            pass
        if not prev_receipt:
            self.fail(identifier, str(error) if isinstance(error, AgentError) else "Aplicação inicial não confirmada.")
            return
        self.indeterminate(identifier, f"Comunicação perdida durante aplicação ({error}); resultado indeterminado até reconciliação.")

    def apply(self, machine, target, envelope):
        identifier = machine["id"]
        # Pre-check identify
        try:
            self.verify(machine, target, self.channel.identify(target))
        except Exception as error:
            self.fail(identifier, str(error))
            return
        # Save candidate before initiating apply
        with self.lock:
            self.records.setdefault(identifier, {})["candidate"] = {
                "project_sha256": envelope["project_sha256"],
                "configuration_revision": envelope["configuration_revision"],
                "envelope": envelope,
            }
            self.save()
        try:
            reply = self.channel.apply(target, envelope)
            self.verify(machine, target, reply)
            self._confirm_apply(machine, target, envelope, reply)
        except Exception as error:
            if isinstance(error, AgentError) and not self._is_communication_loss(error):
                self.fail(identifier, str(error))
                return
            self._handle_apply_error(machine, target, envelope, error)

    def probe(self, identifier):
        state, machine = self.machine(identifier)
        try:
            target = self.target(machine)
            reply = self.channel.identify(target)
            self.verify(machine, target, reply)
            with self.lock:
                record = self.records.setdefault(identifier, {"status": "pending"})
                receipt = reply.get("receipt", {})
                snapshot = reply.get("snapshot", {})
                current_receipt = record.get("receipt", {})
                candidate = record.get("candidate")
                if record.get("status") in ("indeterminate", "applying"):
                    # Check candidate first
                    cand_envelope = candidate.get("envelope") if candidate else export_project(state, identifier, target)
                    if (receipt and cand_envelope
                            and receipt.get("project_sha256") == cand_envelope["project_sha256"]
                            and str(receipt.get("configuration_revision")) == str(cand_envelope["configuration_revision"])):
                        try:
                            self._validate_confirmed_catalog(cand_envelope, receipt, snapshot)
                            record.update(status="applied", fingerprint=fingerprint(machine), receipt=receipt, last_error=None, confirmed_at=utc_now())
                        except Exception as e:
                            record.update(status="indeterminate", last_error=f"Reconciliação inconclusiva: snapshot inválido ({e})")
                    elif (current_receipt and receipt
                            and receipt.get("project_sha256") == current_receipt.get("project_sha256")
                            and str(receipt.get("configuration_revision")) == str(current_receipt.get("configuration_revision"))):
                        record.update(status="failed", last_error="Reconciliação confirmou que a configuração anterior segue ativa.")
                    else:
                        record.update(status="indeterminate", last_error="Reconciliação inconclusiva: destino difere do anterior e do candidato.")
                record.update(identity_hostname=reply["identity"]["hostname"], verified_at=utc_now(),
                              last_error=None if record.get("status") == "applied" else record.get("last_error"))
                self.save()
            return {"deployment": self.status(machine), "identity": reply["identity"]}
        except AgentError as error:
            with self.lock:
                record = self.records.get(identifier, {})
                if record.get("status") == "indeterminate":
                    record.update(last_error=f"Reconciliação pendente: destino inacessível ({error})", checked_at=utc_now())
                    self.save()
                    return {"deployment": self.status(machine)}
            self.fail(identifier, str(error))
            return {"deployment": self.status(machine)}

    def read(self, variables, simulator):
        state = self.store.state(include_deployment=False)
        by_variable = {}; by_module = {}
        for c in state["connections"]:
            for m in c["machines"]:
                for s in m["modules"]:
                    for f in s["folders"]:
                        for v in f["variables"]:
                            by_variable[v["id"]] = m; by_module[v["id"]] = s
        output = []; snapshots = {}
        for variable in variables:
            machine = by_variable[variable["id"]]
            status = self.status(machine)
            if not status["configured"]:
                if by_module[variable["id"]].get("source_kind") == "opc_da":
                    raise ManagementError("Origem OPC DA exige aplicação confirmada ao destino; nenhum valor simulado será apresentado.", 503, "agent_not_applied")
                sample = simulator.read([variable])[0]
                sample.update(origin="Agente simulado Linux", applied_to_windows=False)
            else:
                if status["status"] != "applied":
                    raise ManagementError("Configuração Windows pendente ou falhou; confira a aplicação.", 503, "agent_not_applied")
                try:
                    if machine["id"] not in snapshots:
                        target = self.target(machine); reply = self.channel.snapshot(target); self.verify(machine, target, reply)
                        snapshot = reply["snapshot"]
                        if snapshot.get("config_sha256") != status["receipt"]["project_sha256"] or snapshot.get("machine_id") != machine["id"]: raise AgentError("Runtime diferente do confirmado.")
                        snapshots[machine["id"]] = snapshot
                    snapshot = snapshots[machine["id"]]
                    raw = next(s for s in snapshot["samples"] if s["item_id"] == variable["item_id"])
                    ticks = raw.get("source_timestamp_filetime")
                    timestamp = (dt.datetime(1601, 1, 1, tzinfo=dt.timezone.utc) + dt.timedelta(microseconds=ticks // 10)).isoformat().replace("+00:00", "Z") if ticks else None
                    code = raw["quality"]
                    sample = {"variable_id": variable["id"], "item_id": raw["item_id"], "source_id": variable["source_id"],
                              "data_type": raw["data_type"], "source_data_type": raw.get("source_data_type") or variable.get("source_data_type") or variable["data_type"],
                              "source_conversion": raw.get("source_conversion", variable.get("source_conversion")), "value": raw["value"],
                              "quality": "Good" if code & 192 == 192 else "Uncertain" if code & 192 == 64 else "Bad", "quality_code": code,
                              "source_timestamp": timestamp, "source_timestamp_filetime": ticks, "hresult": raw.get("hresult", 0),
                              "source_vartype": raw.get("source_vartype"), "published_vartype": raw.get("published_vartype", 11 if raw["data_type"] == "Boolean" else 5),
                              "source_kind": "opc_da" if raw.get("source_module_id") else "simulated",
                              "origin": "Agente Windows — OPC DA real" if raw.get("source_module_id") else "Agente Windows — fonte simulada",
                              "applied_to_windows": True, "server_pid": snapshot["pid"]}
                except Exception:
                    raise ManagementError("Agente Windows sem runtime confirmado disponível. Abra/reconecte o cliente OPC DA.", 503, "agent_unavailable")
            output.append(sample)
        return {"simulated": not any(s.get("source_kind") == "opc_da" for s in output), "applied_to_windows": any(s.get("applied_to_windows") for s in output), "samples": output}
