"""Tests for TLS Transport Client and Server."""
from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
import ssl
import subprocess
import pytest

from opc_da_bridge.protocol.messages import (
    DataStreamPayload,
    FrameReader,
    HelloAckPayload,
    HelloPayload,
    ItemDataUpdate,
    MsgType,
    frame_message,
)
from opc_da_bridge.transport.tls import TlsTransportClient, TlsTransportServer


@pytest.fixture
def tls_certs(tmp_path: Path):
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


def test_tls_handshake_and_stream(tls_certs):
    async def _run():
        server_ssl, client_ssl = tls_certs
        token_hash = hashlib.sha256(b"secret_token_123").digest()

        received_batches: list[DataStreamPayload] = []

        async def on_data(stream_payload: DataStreamPayload):
            received_batches.append(stream_payload)

        server = TlsTransportServer(
            host="127.0.0.1",
            port=0,
            ssl_context=server_ssl,
            expected_token_hash=token_hash,
            on_data_received=on_data,
        )
        await server.start()
        port = server.bound_port

        client = TlsTransportClient(
            host="127.0.0.1",
            port=port,
            agent_id="test_agt",
            token_hash=token_hash,
            ssl_context=client_ssl,
            server_hostname="localhost",
        )

        session_id = await client.connect()
        assert session_id.startswith("sess_")
        assert client.is_connected is True

        # Send a data batch matching agent_id and session_id
        batch = DataStreamPayload(
            source_id="test_agt",
            session_id=session_id,
            sequence=1,
            items=[ItemDataUpdate(1, "Tag1", 0, 1, 0xC0, 1000, b"\x01\x00\x00\x00", 0)],
        )
        await client.send_stream(batch)
        await asyncio.sleep(0.05)

        assert len(received_batches) == 1
        assert received_batches[0].sequence == 1
        assert received_batches[0].source_id == "test_agt"
        assert received_batches[0].items[0].opc_item_path == "Tag1"

        await client.disconnect()
        await server.stop()

    asyncio.run(_run())


def test_tls_auth_failure_rejected(tls_certs):
    async def _run():
        server_ssl, client_ssl = tls_certs
        correct_token_hash = hashlib.sha256(b"correct_password").digest()
        wrong_token_hash = hashlib.sha256(b"wrong_password").digest()

        server = TlsTransportServer(
            host="127.0.0.1",
            port=0,
            ssl_context=server_ssl,
            expected_token_hash=correct_token_hash,
        )
        await server.start()
        port = server.bound_port

        client = TlsTransportClient(
            host="127.0.0.1",
            port=port,
            agent_id="bad_agent",
            token_hash=wrong_token_hash,
            ssl_context=client_ssl,
            server_hostname="localhost",
        )

        with pytest.raises((PermissionError, ConnectionError)):
            await client.connect()

        assert client.is_connected is False
        await client.disconnect()
        await server.stop()

    asyncio.run(_run())


def test_tls_disconnect_event_callback(tls_certs):
    async def _run():
        server_ssl, client_ssl = tls_certs
        disconnected_sessions: list[str] = []

        async def on_disconnect(session_id: str):
            disconnected_sessions.append(session_id)

        server = TlsTransportServer(
            host="127.0.0.1",
            port=0,
            ssl_context=server_ssl,
            on_client_disconnected=on_disconnect,
        )
        await server.start()
        port = server.bound_port

        client = TlsTransportClient(
            host="127.0.0.1",
            port=port,
            ssl_context=client_ssl,
            server_hostname="localhost",
        )
        session_id = await client.connect()
        assert client.is_connected is True

        await client.disconnect()
        await asyncio.sleep(0.1)

        assert len(disconnected_sessions) == 1
        assert disconnected_sessions[0] == session_id

        await server.stop()

    asyncio.run(_run())


def test_tls_rejects_spoofed_source_id(tls_certs):
    """Server must reject DATA_STREAM where payload.source_id does not match HELLO.agent_id."""
    async def _run():
        server_ssl, client_ssl = tls_certs
        received_batches: list[DataStreamPayload] = []

        async def on_data(stream_payload: DataStreamPayload):
            received_batches.append(stream_payload)

        server = TlsTransportServer(
            host="127.0.0.1",
            port=0,
            ssl_context=server_ssl,
            on_data_received=on_data,
        )
        await server.start()
        port = server.bound_port

        client = TlsTransportClient(
            host="127.0.0.1",
            port=port,
            agent_id="legit_agent_alpha",
            ssl_context=client_ssl,
            server_hostname="localhost",
        )
        session_id = await client.connect()

        # Send spoofed source_id
        spoofed_batch = DataStreamPayload(
            source_id="spoofed_agent_bravo",
            session_id=session_id,
            sequence=1,
            items=[ItemDataUpdate(1, "Tag1", 0, 1, 0xC0, 1000, b"\x01\x00\x00\x00", 0)],
        )
        await client.send_stream(spoofed_batch)
        await asyncio.sleep(0.05)

        # Must NOT be enqueued or received
        assert len(received_batches) == 0

        # Now send legitimate source_id
        legit_batch = DataStreamPayload(
            source_id="legit_agent_alpha",
            session_id=session_id,
            sequence=2,
            items=[ItemDataUpdate(1, "Tag1", 0, 1, 0xC0, 1000, b"\x01\x00\x00\x00", 0)],
        )
        await client.send_stream(legit_batch)
        await asyncio.sleep(0.05)

        assert len(received_batches) == 1
        assert received_batches[0].source_id == "legit_agent_alpha"

        await client.disconnect()
        await server.stop()

    asyncio.run(_run())


def test_tls_rejects_mismatched_session_id(tls_certs):
    """Server must reject DATA_STREAM where payload.session_id does not match connection's session_id."""
    async def _run():
        server_ssl, client_ssl = tls_certs
        received_batches: list[DataStreamPayload] = []

        async def on_data(stream_payload: DataStreamPayload):
            received_batches.append(stream_payload)

        server = TlsTransportServer(
            host="127.0.0.1",
            port=0,
            ssl_context=server_ssl,
            on_data_received=on_data,
        )
        await server.start()
        port = server.bound_port

        client = TlsTransportClient(
            host="127.0.0.1",
            port=port,
            agent_id="legit_agent",
            ssl_context=client_ssl,
            server_hostname="localhost",
        )
        session_id = await client.connect()

        # Send mismatched session_id
        fake_sess_batch = DataStreamPayload(
            source_id="legit_agent",
            session_id="sess_completely_fake_id",
            sequence=1,
            items=[ItemDataUpdate(1, "Tag1", 0, 1, 0xC0, 1000, b"\x01\x00\x00\x00", 0)],
        )
        await client.send_stream(fake_sess_batch)
        await asyncio.sleep(0.05)

        # Must NOT be enqueued or received
        assert len(received_batches) == 0

        # Now send matching session_id
        valid_batch = DataStreamPayload(
            source_id="legit_agent",
            session_id=session_id,
            sequence=2,
            items=[ItemDataUpdate(1, "Tag1", 0, 1, 0xC0, 1000, b"\x01\x00\x00\x00", 0)],
        )
        await client.send_stream(valid_batch)
        await asyncio.sleep(0.05)

        assert len(received_batches) == 1
        assert received_batches[0].session_id == session_id

        await client.disconnect()
        await server.stop()

    asyncio.run(_run())


def test_tls_fragmented_hello_handshake(tls_certs):
    """Server must reassemble fragmented HELLO frames across multiple chunk reads."""
    async def _run():
        server_ssl, client_ssl = tls_certs

        server = TlsTransportServer(
            host="127.0.0.1",
            port=0,
            ssl_context=server_ssl,
        )
        await server.start()
        port = server.bound_port

        # Connect directly using raw stream to inject tiny fragments
        reader, writer = await asyncio.open_connection(
            "127.0.0.1",
            port,
            ssl=client_ssl,
            server_hostname="localhost",
        )

        hello_payload = HelloPayload(
            agent_id="fragmented_agent",
            hostname="host_frag",
            os_version="Linux",
            capabilities=["test_v2"],
        )
        raw_hello_frame = frame_message(MsgType.HELLO, 0, hello_payload.pack())

        # Send HELLO in tiny 7-byte chunks
        chunk_size = 7
        for i in range(0, len(raw_hello_frame), chunk_size):
            chunk = raw_hello_frame[i : i + chunk_size]
            writer.write(chunk)
            await writer.drain()
            await asyncio.sleep(0.005)

        # Read HELLO_ACK from server
        frame_reader = FrameReader()
        while True:
            chunk = await reader.read(4096)
            assert chunk, "Server closed connection unexpectedly on fragmented HELLO"
            frame_reader.feed(chunk)
            frame = frame_reader.next_frame()
            if frame:
                break

        assert frame[0].msg_type == MsgType.HELLO_ACK
        ack = HelloAckPayload.unpack(frame[1])
        assert ack.session_id.startswith("sess_")

        writer.close()
        await writer.wait_closed()
        await server.stop()

    asyncio.run(_run())
