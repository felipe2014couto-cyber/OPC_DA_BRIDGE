"""Versioned TLS Transport for OPC-DA-Bridge.

Provides asyncio-based TLS client and server with server certificate validation,
shared-token hash authentication, framing validation, and disconnect event notifications
(with mutual TLS / mTLS configurable via SSLContext).
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import ssl
from typing import Any, Callable, Coroutine

from opc_da_bridge.core.model import utc_now_us
from opc_da_bridge.protocol.messages import (
    AuthAckPayload,
    AuthPayload,
    DataStreamPayload,
    FrameReader,
    Header,
    HelloAckPayload,
    HelloPayload,
    MsgType,
    frame_message,
)

logger = logging.getLogger(__name__)


class TlsTransportServer:
    """TLS server listening for incoming source connections and dispatching data."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 0,
        ssl_context: ssl.SSLContext | None = None,
        expected_token_hash: bytes | None = None,
        on_data_received: Callable[[DataStreamPayload], Coroutine[Any, Any, None]] | None = None,
        on_client_connected: Callable[[str, str], Coroutine[Any, Any, None]] | None = None,
        on_client_disconnected: Callable[[str], Coroutine[Any, Any, None]] | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.ssl_context = ssl_context
        self.expected_token_hash = expected_token_hash
        self.on_data_received = on_data_received
        self.on_client_connected = on_client_connected
        self.on_client_disconnected = on_client_disconnected

        self._server: asyncio.Server | None = None
        self._active_sessions: dict[str, asyncio.StreamWriter] = {}
        self._serving = False

    @property
    def bound_port(self) -> int:
        if self._server and self._server.sockets:
            return self._server.sockets[0].getsockname()[1]
        return self.port

    async def start(self) -> None:
        """Start listening on configured host and port with TLS."""
        self._serving = True
        self._server = await asyncio.start_server(
            self._handle_client,
            host=self.host,
            port=self.port,
            ssl=self.ssl_context,
        )
        logger.info(f"TlsTransportServer listening on {self.host}:{self.bound_port}")

    async def stop(self) -> None:
        """Stop listening and close all active client sessions."""
        self._serving = False
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

        # Close all active connections
        for session_id, writer in list(self._active_sessions.items()):
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
        self._active_sessions.clear()

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        session_id = f"sess_{hashlib.sha256(str(asyncio.get_event_loop().time()).encode()).hexdigest()[:12]}"
        frame_reader = FrameReader()
        seq_out = 0

        try:
            # 1. Expect HELLO (read in a loop to support fragmented TLS frames)
            while True:
                frame = frame_reader.next_frame()
                if frame is not None:
                    break
                chunk = await reader.read(4096)
                if not chunk:
                    writer.close()
                    await writer.wait_closed()
                    return
                frame_reader.feed(chunk)

            if frame[0].msg_type != MsgType.HELLO:
                writer.close()
                await writer.wait_closed()
                return

            hello = HelloPayload.unpack(frame[1])
            bound_agent_id = hello.agent_id
            bound_session_id = session_id
            logger.info(
                f"Accepted HELLO from agent {bound_agent_id} ({hello.hostname}), "
                f"bound session {bound_session_id}"
            )

            # Reply HELLO_ACK
            hello_ack = HelloAckPayload(session_id=session_id, heartbeat_interval_ms=5000)
            writer.write(frame_message(MsgType.HELLO_ACK, seq_out, hello_ack.pack()))
            await writer.drain()
            seq_out += 1

            # 2. Expect AUTH if token configured
            if self.expected_token_hash:
                while True:
                    frame = frame_reader.next_frame()
                    if frame:
                        break
                    chunk = await reader.read(4096)
                    if not chunk:
                        return
                    frame_reader.feed(chunk)

                if frame[0].msg_type != MsgType.AUTH:
                    writer.close()
                    await writer.wait_closed()
                    return

                auth = AuthPayload.unpack(frame[1])
                success = (auth.token_hash == self.expected_token_hash)
                auth_ack = AuthAckPayload(success=success, policies_version=1)
                writer.write(frame_message(MsgType.AUTH_ACK, seq_out, auth_ack.pack()))
                await writer.drain()
                seq_out += 1

                if not success:
                    writer.close()
                    await writer.wait_closed()
                    return

            self._active_sessions[session_id] = writer
            if self.on_client_connected:
                await self.on_client_connected(bound_agent_id, session_id)

            # 3. Stream processing loop
            while self._serving:
                frame = frame_reader.next_frame()
                if frame is None:
                    chunk = await reader.read(8192)
                    if not chunk:
                        break  # EOF / Client disconnected
                    frame_reader.feed(chunk)
                    continue

                hdr, payload = frame
                if hdr.msg_type == MsgType.DATA_STREAM:
                    stream_data = DataStreamPayload.unpack(payload)
                    # Enforce identity and session binding for this connection
                    if stream_data.source_id != bound_agent_id or stream_data.session_id != bound_session_id:
                        logger.warning(
                            f"Rejected DATA_STREAM with mismatched identity or session: "
                            f"connection bound=({bound_agent_id}, {bound_session_id}), "
                            f"payload=({stream_data.source_id}, {stream_data.session_id})"
                        )
                        continue
                    if self.on_data_received:
                        await self.on_data_received(stream_data)
                elif hdr.msg_type == MsgType.HEARTBEAT:
                    # Echo heartbeat back
                    writer.write(frame_message(MsgType.HEARTBEAT, seq_out, payload))
                    await writer.drain()
                    seq_out += 1
                elif hdr.msg_type == MsgType.DISCONNECT_NOTICE:
                    logger.info(f"Client notified disconnect for {session_id}")
                    break

        except (asyncio.IncompleteReadError, ConnectionResetError, ssl.SSLError):
            pass
        except Exception as exc:
            logger.warning(f"Error handling transport client: {exc}")
        finally:
            self._active_sessions.pop(session_id, None)
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            if self.on_client_disconnected:
                await self.on_client_disconnected(session_id)


class TlsTransportClient:
    """TLS client establishing an authenticated stream to the destination server."""

    def __init__(
        self,
        host: str,
        port: int,
        agent_id: str = "sim_source_1",
        token_hash: bytes | None = None,
        ssl_context: ssl.SSLContext | None = None,
        server_hostname: str | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.agent_id = agent_id
        self.token_hash = token_hash
        self.ssl_context = ssl_context
        self.server_hostname = server_hostname

        self.session_id: str | None = None
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._frame_reader = FrameReader()
        self._seq_out = 0
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected and self._writer is not None and not self._writer.is_closing()

    async def connect(self) -> str:
        """Establish TLS connection and complete handshake. Returns session_id."""
        self._reader, self._writer = await asyncio.open_connection(
            self.host,
            self.port,
            ssl=self.ssl_context,
            server_hostname=self.server_hostname,
        )

        # 1. Send HELLO
        hello = HelloPayload(
            agent_id=self.agent_id,
            hostname="localhost",
            os_version="Linux",
            capabilities=["opc_da_mirror_v2"],
        )
        self._writer.write(frame_message(MsgType.HELLO, self._seq_out, hello.pack()))
        await self._writer.drain()
        self._seq_out += 1

        # Await HELLO_ACK
        while True:
            chunk = await self._reader.read(4096)
            if not chunk:
                raise ConnectionError("Connection closed during HELLO handshake")
            self._frame_reader.feed(chunk)
            frame = self._frame_reader.next_frame()
            if frame:
                break

        if frame[0].msg_type != MsgType.HELLO_ACK:
            raise ConnectionError(f"Unexpected response to HELLO: {frame[0].msg_type}")

        hello_ack = HelloAckPayload.unpack(frame[1])
        self.session_id = hello_ack.session_id

        # 2. Send AUTH if token configured
        if self.token_hash:
            auth = AuthPayload(token_hash=self.token_hash)
            self._writer.write(frame_message(MsgType.AUTH, self._seq_out, auth.pack()))
            await self._writer.drain()
            self._seq_out += 1

            while True:
                chunk = await self._reader.read(4096)
                if not chunk:
                    raise ConnectionError("Connection closed during AUTH handshake")
                self._frame_reader.feed(chunk)
                frame = self._frame_reader.next_frame()
                if frame:
                    break

            if frame[0].msg_type != MsgType.AUTH_ACK:
                raise ConnectionError("Authentication rejected or invalid message")

            auth_ack = AuthAckPayload.unpack(frame[1])
            if not auth_ack.success:
                raise PermissionError("Authentication failed: invalid token hash")

        self._connected = True
        return self.session_id

    async def send_stream(self, data_stream: DataStreamPayload) -> None:
        """Send a DataStreamPayload message over the TLS transport."""
        if not self.is_connected or self._writer is None:
            raise ConnectionError("Transport client is not connected")

        payload_bytes = data_stream.pack()
        framed = frame_message(MsgType.DATA_STREAM, self._seq_out, payload_bytes)
        self._writer.write(framed)
        await self._writer.drain()
        self._seq_out += 1

    async def disconnect(self) -> None:
        """Gracefully disconnect from destination server."""
        self._connected = False
        if self._writer and not self._writer.is_closing():
            try:
                self._writer.write(frame_message(MsgType.DISCONNECT_NOTICE, self._seq_out, b""))
                await self._writer.drain()
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
        self._reader = None
        self._writer = None
