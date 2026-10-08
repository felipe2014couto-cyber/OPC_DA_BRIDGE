"""Integration tests for the complete OPC-DA-Bridge pipeline."""
from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
import ssl
import subprocess
import pytest

from opc_da_bridge.core.cache import BoundedLatestValueCache
from opc_da_bridge.core.model import (
    OPC_QUALITY_BAD_COMM_FAILURE,
    OPC_QUALITY_BAD_NOT_CONNECTED,
    OPC_QUALITY_GOOD,
    ValueType,
    is_quality_good,
)
from opc_da_bridge.destination.receiver import DestinationReceiver
from opc_da_bridge.source.simulated_source import SimulatedSource
from opc_da_bridge.transport.tls import TlsTransportClient, TlsTransportServer


@pytest.fixture
def cert_pair(tmp_path: Path):
    certfile = tmp_path / "cert.pem"
    keyfile = tmp_path / "key.pem"
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048",
            "-keyout", str(keyfile), "-out", str(certfile),
            "-days", "1", "-nodes", "-subj", "/CN=localhost",
            "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1",
        ],
        check=True, capture_output=True,
    )
    server_ssl = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ssl.load_cert_chain(str(certfile), str(keyfile))

    client_ssl = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=str(certfile))
    client_ssl.check_hostname = True
    return server_ssl, client_ssl


def test_full_pipeline_streaming_and_disconnect(cert_pair):
    async def _run():
        server_ssl, client_ssl = cert_pair
        token_hash = hashlib.sha256(b"pipeline_token").digest()

        cache = BoundedLatestValueCache(max_items=50, stale_timeout_s=1.0)
        receiver = DestinationReceiver(cache=cache, stale_check_interval_s=0.1)
        await receiver.start()

        server = TlsTransportServer(
            host="127.0.0.1",
            port=0,
            ssl_context=server_ssl,
            expected_token_hash=token_hash,
            on_data_received=receiver.on_data_received,
            on_client_connected=receiver.on_client_connected,
            on_client_disconnected=receiver.on_client_disconnected,
        )
        await server.start()
        port = server.bound_port

        source = SimulatedSource(source_id="sim_source_1")
        client = TlsTransportClient(
            host="127.0.0.1",
            port=port,
            agent_id="sim_source_1",
            token_hash=token_hash,
            ssl_context=client_ssl,
            server_hostname="localhost",
        )

        session_id = await client.connect()
        source.session_id = session_id

        # Stream 3 batches
        for _ in range(3):
            await client.send_stream(source.next_batch())
            await asyncio.sleep(0.02)

        await asyncio.sleep(0.1)
        assert cache.size == 6

        # Verify all items in cache
        for item in cache.get_all():
            assert item.quality == OPC_QUALITY_GOOD
            assert is_quality_good(item.quality)
            assert item.sequence == 3
            assert item.session_id == session_id
            assert item.source_timestamp_us > 0

        # Disconnect client
        await client.disconnect()
        await asyncio.sleep(0.15)

        # Verify all items transitioned to Bad [Not Connected] while preserving values
        for item in cache.get_all():
            assert item.quality == OPC_QUALITY_BAD_NOT_CONNECTED
            assert item.disconnected is True
            assert item.stale is True
            assert item.source_timestamp_us > 0
            assert item.value is not None

        await receiver.stop()
        await server.stop()

    asyncio.run(_run())


def test_receiver_bounded_queue_overflow():
    async def _run():
        cache = BoundedLatestValueCache(max_items=10)
        # Very small queue of size 2
        receiver = DestinationReceiver(cache=cache, queue_maxsize=2)
        source = SimulatedSource(session_id="s1", source_id="sim_source_1")

        # Enqueue 5 batches
        for _ in range(5):
            await receiver.on_data_received(source.next_batch())

        assert receiver.metrics["batches_dropped_queue_full"] >= 3
        assert receiver._queue.qsize() <= 2

    asyncio.run(_run())


def test_receiver_queue_overflow_join_does_not_hang():
    """Verify that when queue drops oldest batches on overflow, task_done() is balanced
    so that queue.join() completes cleanly and does not hang."""
    async def _run():
        cache = BoundedLatestValueCache(max_items=10)
        cache.bind_active_session("sim_source_1", "s1")
        receiver = DestinationReceiver(cache=cache, queue_maxsize=2)
        source = SimulatedSource(session_id="s1", source_id="sim_source_1")

        # Push 6 batches with worker paused, forcing 4 batches to be dropped on overflow
        for _ in range(6):
            await receiver.on_data_received(source.next_batch())

        assert receiver.metrics["batches_dropped_queue_full"] == 4
        assert receiver._queue.qsize() == 2

        # Start worker so it drains the remaining 2 batches in the queue
        await receiver.start()

        # join() MUST complete promptly without hanging because dropped batches called task_done()
        await asyncio.wait_for(receiver._queue.join(), timeout=2.0)

        assert receiver._queue.qsize() == 0
        await receiver.stop()

    asyncio.run(_run())
