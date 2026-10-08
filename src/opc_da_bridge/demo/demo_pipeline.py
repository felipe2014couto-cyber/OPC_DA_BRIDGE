"""End-to-end demonstration of OPC-DA-Bridge pipeline.

Simulated source -> versioned TLS transport -> destination receiver -> bounded latest-value cache.
Demonstrates:
- Live streaming across canonical OPC DA data types (F64, F32, I32, BOOL, STRING, I16).
- Preservation of value, type, original source UTC timestamp, session ID, sequence.
- Monotonic sequence enforcement: duplicate idempotence and out-of-order rejection.
- Disconnect handling: automatic transition to Bad [Not Connected] preserving last timestamp.
- Disk restore behavior: items restored as Bad [Last Known Value] (never Good).
- Transport security: server certificate verification and shared-token hash authentication
  (with mutual TLS / mTLS available as a configurable option when client certificates are loaded).
"""
from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path
import ssl
import subprocess
import tempfile
import time

from opc_da_bridge.core.cache import BoundedLatestValueCache
from opc_da_bridge.core.model import (
    OPC_QUALITY_BAD_LAST_KNOWN_VALUE,
    OPC_QUALITY_BAD_NOT_CONNECTED,
    format_opc_timestamp,
    format_quality_text,
    is_quality_good,
)
from opc_da_bridge.destination.receiver import DestinationReceiver
from opc_da_bridge.source.simulated_source import SimulatedSource
from opc_da_bridge.transport.tls import TlsTransportClient, TlsTransportServer


def generate_self_signed_cert(cert_dir: Path) -> tuple[str, str]:
    cert_path = cert_dir / "server_cert.pem"
    key_path = cert_dir / "server_key.pem"
    cmd = [
        "openssl", "req", "-x509", "-newkey", "rsa:2048",
        "-keyout", str(key_path), "-out", str(cert_path),
        "-days", "1", "-nodes", "-subj", "/CN=localhost",
        "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1",
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return str(cert_path), str(key_path)


async def run_demo() -> None:
    print("=" * 80)
    print("OPC-DA-BRIDGE END-TO-END DEMO: SOURCE -> TLS TRANSPORT -> BOUNDED CACHE")
    print("=" * 80)

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        cert_file, key_file = generate_self_signed_cert(tmp_path)

        # 1. Setup TLS contexts: server certificate verification + shared-token hash auth
        server_ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_ssl_ctx.load_cert_chain(cert_file, key_file)

        client_ssl_ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=cert_file)
        client_ssl_ctx.check_hostname = True

        token_hash = hashlib.sha256(b"TEST_ONLY_shared_token").digest()

        # 2. Initialize destination components
        cache = BoundedLatestValueCache(max_items=1000, stale_timeout_s=3.0)
        receiver = DestinationReceiver(cache=cache)
        await receiver.start()

        server = TlsTransportServer(
            host="127.0.0.1",
            port=0,
            ssl_context=server_ssl_ctx,
            expected_token_hash=token_hash,
            on_data_received=receiver.on_data_received,
            on_client_connected=receiver.on_client_connected,
            on_client_disconnected=receiver.on_client_disconnected,
        )
        await server.start()
        port = server.bound_port
        print(f"[*] TLS Destination Server listening on 127.0.0.1:{port}")

        # 3. Initialize simulated source and TLS client
        source = SimulatedSource(session_id="session_turbine_rig_01", source_id="source_rig_01")
        client = TlsTransportClient(
            host="127.0.0.1",
            port=port,
            agent_id="source_rig_01",
            token_hash=token_hash,
            ssl_context=client_ssl_ctx,
            server_hostname="localhost",
        )

        session_id = await client.connect()
        source.session_id = session_id
        print(f"[*] TLS Transport Client connected successfully. Handshake session: {session_id}")

        # 4. Stream 3 batches of telemetry
        print("\n--- Phase 1: Live Telemetry Streaming over TLS ---")
        for i in range(3):
            batch = source.next_batch()
            await client.send_stream(batch)
            await asyncio.sleep(0.05)  # allow receiver worker to process

        # Wait for queue drain
        await asyncio.sleep(0.1)

        print(f"[*] Cache populated with {cache.size} items.")
        print("-" * 80)
        print(f"{'Item Path':<32} {'Type':<8} {'Value':<18} {'Quality':<12} {'Seq':<5} {'Source UTC Time'}")
        print("-" * 80)
        for item in cache.get_all():
            val_str = f"{item.value:.3f}" if isinstance(item.value, float) else str(item.value)
            ts_str = format_opc_timestamp(item.source_timestamp_us) or "N/A"
            q_str = format_quality_text(item.quality)
            print(f"{item.opc_item_path:<32} {item.value_type:<8} {val_str:<18} {q_str:<12} {item.sequence:<5} {ts_str}")

        # 5. Monotonic sequence validation (duplicates and out-of-order)
        print("\n--- Phase 2: Monotonic Sequence Validation ---")
        # Duplicate: resend sequence 3
        dup_batch = source.next_batch(force_seq=3)
        await client.send_stream(dup_batch)
        await asyncio.sleep(0.05)
        print(f"[*] Sent duplicate seq=3. Duplicates ignored count: {cache.stats['duplicates_ignored']}")

        # Out-of-order: send sequence 1 while at sequence 3
        ooo_batch = source.next_batch(force_seq=1)
        await client.send_stream(ooo_batch)
        await asyncio.sleep(0.05)
        print(f"[*] Sent out-of-order seq=1. Out-of-order rejected count: {cache.stats['out_of_order_rejected']}")

        # 6. Disconnect handling
        print("\n--- Phase 3: Disconnect Handling ---")
        print("[*] Disconnecting TLS source client...")
        await client.disconnect()
        await asyncio.sleep(0.15)  # wait for disconnect event propagation

        print("[*] Evaluating cache status post-disconnect:")
        print("-" * 80)
        print(f"{'Item Path':<32} {'Last Known Value':<18} {'Quality':<26} {'Preserved Source Timestamp'}")
        print("-" * 80)
        for item in cache.get_all():
            val_str = f"{item.value:.3f}" if isinstance(item.value, float) else str(item.value)
            ts_str = format_opc_timestamp(item.source_timestamp_us) or "N/A"
            q_str = format_quality_text(item.quality)
            print(f"{item.opc_item_path:<32} {val_str:<18} {q_str:<26} {ts_str}")
            assert item.quality == OPC_QUALITY_BAD_NOT_CONNECTED
            assert item.disconnected is True
            assert item.source_timestamp_us > 0

        # 7. Disk Snapshot & Restart Behavior
        print("\n--- Phase 4: Disk Snapshot Restore (Never Good on Restart) ---")
        snapshot_file = tmp_path / "cache_snapshot.json"
        cache.save_snapshot(snapshot_file)
        print(f"[*] Cache snapshot written to {snapshot_file.name}")

        new_cache = BoundedLatestValueCache(max_items=1000)
        restored_count = new_cache.load_snapshot(snapshot_file)
        print(f"[*] New cold-started cache restored {restored_count} items from disk.")
        for item in new_cache.get_all():
            q_str = format_quality_text(item.quality)
            assert item.quality == OPC_QUALITY_BAD_LAST_KNOWN_VALUE
            assert not is_quality_good(item.quality)
            assert item.stale is True

        print(f"[*] Verified all restored items have quality '{format_quality_text(OPC_QUALITY_BAD_LAST_KNOWN_VALUE)}' (strictly non-Good).")

        # Cleanup
        await receiver.stop()
        await server.stop()
        print("\n" + "=" * 80)
        print("DEMO PIPELINE COMPLETED SUCCESSFULLY WITH ZERO ERRORS.")
        print("=" * 80)


if __name__ == "__main__":
    asyncio.run(run_demo())
