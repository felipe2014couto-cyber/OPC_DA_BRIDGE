"""CLI entry point for the isolated DA-to-UA lab gateway."""
from __future__ import annotations

import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import ipaddress
import json
import logging
from pathlib import Path

from opc_da_bridge.core.cache import BoundedLatestValueCache
from opc_da_bridge.core.model import ValueType
from opc_da_bridge.lab.gateway import Gateway, Pywin32DaSource, TagConfig, da_quality_to_ua


def validate_endpoint_binding(host: str, security_mode: str) -> None:
    """Prevent the unauthenticated lab endpoint from binding outside loopback."""
    try:
        is_loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        is_loopback = host.lower() == "localhost"
    if security_mode not in {"local_no_security", "sign_and_encrypt"}:
        raise ValueError("opc_ua.security.mode must be local_no_security or sign_and_encrypt")
    if security_mode == "local_no_security" and not is_loopback:
        raise ValueError("NoSecurity mode permits only a loopback OPC UA bind address")
    if security_mode == "sign_and_encrypt" and host in {"0.0.0.0", "::", ""}:
        raise ValueError("secure mode requires an explicit interface address, not a wildcard bind")


async def run(config_path: Path) -> None:
    try:
        from asyncua import Server, ua
    except ImportError as exc:
        raise SystemExit("Install optional lab dependencies: pip install -e .[lab]") from exc
    config = json.loads(config_path.read_text(encoding="utf-8"))
    host = config["opc_ua"]["host"]
    security = config.get("opc_ua", {}).get("security", {"mode": "local_no_security"})
    security_mode = security.get("mode", "local_no_security")
    try:
        validate_endpoint_binding(host, security_mode)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if not 1 <= int(config["opc_ua"]["port"]) <= 65535:
        raise SystemExit("OPC UA port must be between 1 and 65535")
    interval = float(config["update_interval_s"])
    if interval <= 0:
        raise SystemExit("update_interval_s must be positive")
    if not config.get("tags"):
        raise SystemExit("at least one OPC DA tag must be configured")
    item_ids = [row["item_id"] for row in config["tags"]]
    if len(item_ids) != len(set(item_ids)):
        raise SystemExit("OPC DA item IDs must be unique")
    node_ids = [row["node_id"] for row in config["tags"]]
    if len(node_ids) != len(set(node_ids)):
        raise SystemExit("OPC UA node IDs must be unique")
    logger = logging.getLogger("opc_da_bridge.lab")
    logger.setLevel(logging.INFO)
    handler = logging.FileHandler(config.get("log_file", "opc-da-ua-lab.log"), encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    tags = [TagConfig(row["item_id"], row["node_id"]) for row in config["tags"]]
    cache = BoundedLatestValueCache(max_items=max(100, len(tags)), stale_timeout_s=interval * 2.5)
    source = Pywin32DaSource()
    gateway = Gateway(source, cache, tags, config["opc_da"]["prog_id"], logger)

    cert_user_manager = None
    validator = None
    if security_mode == "sign_and_encrypt":
        try:
            from asyncua.crypto.truststore import TrustStore
            from asyncua.crypto.validator import CertificateValidator, CertificateValidatorOptions
            from asyncua.server.user_managers import CertificateUserManager
        except ImportError as exc:
            raise SystemExit("asyncua 1.1.8 certificate authentication support is required") from exc
        for field in ("server_certificate", "server_private_key", "trusted_client_certificates_dir"):
            if not security.get(field):
                raise SystemExit(f"opc_ua.security.{field} is required in sign_and_encrypt mode")
        server_cert = Path(security["server_certificate"])
        server_key = Path(security["server_private_key"])
        client_certs_dir = Path(security["trusted_client_certificates_dir"])
        client_certs = sorted(client_certs_dir.glob("*.der"))
        if not server_cert.is_file() or not server_key.is_file():
            raise SystemExit("configured server certificate/private key file does not exist")
        if not client_certs:
            raise SystemExit("trusted client certificate directory must contain an enrolled .der certificate")
        trust_store = TrustStore([client_certs_dir], [])
        await trust_store.load()
        validator = CertificateValidator(
            options=CertificateValidatorOptions.TRUSTED_VALIDATION | CertificateValidatorOptions.PEER_CLIENT,
            trust_store=trust_store,
        )
        cert_user_manager = CertificateUserManager()
        for cert_path in client_certs:
            await cert_user_manager.add_user(str(cert_path), name=cert_path.stem)

    server = Server(user_manager=cert_user_manager) if cert_user_manager else Server()
    await server.init()
    server.set_endpoint(f"opc.tcp://{host}:{config['opc_ua']['port']}/opc-da-bridge/lab/")
    server.set_server_name("OPC DA Bridge Laboratory Gateway")
    if security_mode == "local_no_security":
        server.set_security_policy([ua.SecurityPolicyType.NoSecurity])
    else:
        if security.get("policy", "Basic256Sha256") != "Basic256Sha256":
            raise SystemExit("only the Basic256Sha256 policy is currently configured for secure mode")
        if not security.get("application_uri"):
            raise SystemExit("opc_ua.security.application_uri is required in sign_and_encrypt mode")
        await server.set_application_uri(security["application_uri"])
        await server.load_certificate(str(server_cert))
        await server.load_private_key(str(server_key))
        server.set_security_policy([ua.SecurityPolicyType.Basic256Sha256_SignAndEncrypt])
        server.set_identity_tokens([ua.X509IdentityToken])
        server.set_certificate_validator(validator)
    idx = await server.register_namespace(config["opc_ua"]["namespace_uri"])
    nodes = {}
    published_types = {}
    for tag in tags:
        browse_name = ua.QualifiedName(tag.item_id, idx)
        node = await server.nodes.objects.add_variable(
            ua.NodeId(tag.node_id, idx), browse_name, None, datatype=ua.ObjectIds.BaseDataType
        )
        await node.set_writable(False)
        await node.write_value(ua.DataValue(ua.Variant(None), ua.StatusCode(0x80310000)))
        nodes[tag.item_id] = node

    async with server:
        if security_mode == "local_no_security":
            logger.warning("OPC UA NoSecurity endpoint active for isolated laboratory use")
        else:
            logger.info("OPC UA endpoint active with Basic256Sha256 SignAndEncrypt and trusted certificate identities")
        # Keep synchronous COM calls off the UA event loop and on one COM apartment thread.
        com_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="opc-da-com")
        try:
            while True:
                await asyncio.get_running_loop().run_in_executor(com_executor, gateway.poll)
                for tag in tags:
                    item = cache.snapshot_batch([tag.item_id])[tag.item_id]
                    if item is None:
                        continue
                    timestamp = None
                    if item.source_timestamp_us > 0:
                        timestamp = datetime.fromtimestamp(item.source_timestamp_us / 1_000_000, timezone.utc)
                    status = da_quality_to_ua(item.quality, gateway.connected, item.stale)
                    value_type = ValueType(item.value_type)
                    ua_variant_type, ua_data_type = {
                        ValueType.I16: (ua.VariantType.Int16, ua.ObjectIds.Int16),
                        ValueType.I32: (ua.VariantType.Int32, ua.ObjectIds.Int32),
                        ValueType.F32: (ua.VariantType.Float, ua.ObjectIds.Float),
                        ValueType.F64: (ua.VariantType.Double, ua.ObjectIds.Double),
                        ValueType.BOOL: (ua.VariantType.Boolean, ua.ObjectIds.Boolean),
                        ValueType.STRING: (ua.VariantType.String, ua.ObjectIds.String),
                        ValueType.BLOB: (ua.VariantType.ByteString, ua.ObjectIds.ByteString),
                    }[value_type]
                    if published_types.get(tag.item_id) != ua_variant_type:
                        await nodes[tag.item_id].write_attribute(
                            ua.AttributeIds.DataType,
                            ua.DataValue(ua.Variant(ua.NodeId(ua_data_type))),
                        )
                        published_types[tag.item_id] = ua_variant_type
                    dv = ua.DataValue(
                        ua.Variant(item.value, ua_variant_type), ua.StatusCode(status), SourceTimestamp=timestamp
                    )
                    await nodes[tag.item_id].write_value(dv)
                await asyncio.sleep(interval)
        finally:
            await asyncio.get_running_loop().run_in_executor(com_executor, source.close)
            com_executor.shutdown(wait=True)
            logger.info("OPC DA source closed")


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only OPC DA to OPC UA lab gateway")
    parser.add_argument("--config", default="lab-config.json")
    args = parser.parse_args()
    asyncio.run(run(Path(args.config)))


if __name__ == "__main__":
    main()
