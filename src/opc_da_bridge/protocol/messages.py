"""OPC-DA-Bridge binary protocol framing and message definitions.

Adapted and versioned from OPC-Bridge (commit 54d7fdfe1e56a4d910980fb1109ba1f2c2b9a757).
Versioned protocol (VERSION = 0x0002) supporting telemetry stream framing,
session sequencing, and heartbeat keepalive over TLS.

PROTOCOL COMPATIBILITY NOTICE:
The OPC-DA-Bridge protocol version 0x0002 is a dedicated, separate bridge
protocol designed for monotonic streaming to the local mirror cache. Existing
OPC-Bridge production agents/servers operating on version 0x0001 (such as ABB
OPC DA collector agents expecting READ_REQUEST/READ_RESPONSE polling) do not
automatically interoperate with v2 streaming endpoints without explicit
protocol translation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
import struct
import zlib
from typing import Any

from opc_da_bridge.core.model import (
    ItemRef,
    ItemSample,
    ItemStatus,
    ValueType,
    decode_value,
    encode_value,
)

MAGIC = 0x4F50  # "OP"
VERSION_V1 = 0x0001
VERSION = 0x0002  # Versioned for OPC-DA-Bridge stream delivery
HEADER_FORMAT = "<HHHHII"  # magic, version, msg_type, flags, seq, payload_len
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)
TRAILER_FORMAT = "<I"  # CRC32 checksum
TRAILER_SIZE = struct.calcsize(TRAILER_FORMAT)


class MsgType(IntEnum):
    HELLO = 0x01
    HELLO_ACK = 0x02
    AUTH = 0x03
    AUTH_ACK = 0x04
    CONFIG_PUSH = 0x05
    CONFIG_ACK = 0x06
    READ_REQUEST = 0x07
    READ_RESPONSE = 0x08
    HEARTBEAT = 0x09
    ERROR = 0x0A
    DATA_STREAM = 0x0D
    DISCONNECT_NOTICE = 0x0E


@dataclass
class Header:
    magic: int = MAGIC
    version: int = VERSION
    msg_type: int = 0
    flags: int = 0
    seq: int = 0
    payload_len: int = 0

    def pack(self) -> bytes:
        return struct.pack(
            HEADER_FORMAT,
            self.magic,
            self.version,
            self.msg_type,
            self.flags,
            self.seq,
            self.payload_len,
        )

    @classmethod
    def unpack(cls, data: bytes, allow_v1: bool = True) -> Header:
        if len(data) != HEADER_SIZE:
            raise ValueError(f"Header must be {HEADER_SIZE} bytes, got {len(data)}")
        magic, version, msg_type, flags, seq, payload_len = struct.unpack(HEADER_FORMAT, data)
        if magic != MAGIC:
            raise ValueError(f"Invalid magic: 0x{magic:04X}")
        if version != VERSION and not (allow_v1 and version == VERSION_V1):
            raise ValueError(f"Unsupported protocol version: 0x{version:04X}")
        return cls(
            magic=magic,
            version=version,
            msg_type=msg_type,
            flags=flags,
            seq=seq,
            payload_len=payload_len,
        )


def _crc32(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF


def frame_message(
    msg_type: MsgType | int,
    seq: int,
    payload: bytes,
    flags: int = 0,
    version: int = VERSION,
) -> bytes:
    """Frame a payload into a complete protocol packet."""
    header = Header(
        magic=MAGIC,
        version=version,
        msg_type=int(msg_type),
        flags=flags,
        seq=seq,
        payload_len=len(payload),
    )
    hdr_bytes = header.pack()
    crc = _crc32(hdr_bytes + payload)
    trailer = struct.pack(TRAILER_FORMAT, crc)
    return hdr_bytes + payload + trailer


def unframe_message(data: bytes, allow_v1: bool = True) -> tuple[Header, bytes]:
    """Parse a single framed message. Raises ValueError on any corruption."""
    if len(data) < HEADER_SIZE + TRAILER_SIZE:
        raise ValueError("Message too short")
    header = Header.unpack(data[:HEADER_SIZE], allow_v1=allow_v1)
    expected_total = HEADER_SIZE + header.payload_len + TRAILER_SIZE
    if len(data) < expected_total:
        raise ValueError(f"Incomplete message: expected {expected_total}, got {len(data)}")
    payload = data[HEADER_SIZE : HEADER_SIZE + header.payload_len]
    trailer_start = HEADER_SIZE + header.payload_len
    received_crc = struct.unpack(TRAILER_FORMAT, data[trailer_start:expected_total])[0]
    computed_crc = _crc32(data[:trailer_start])
    if received_crc != computed_crc:
        raise ValueError(f"CRC mismatch: expected 0x{computed_crc:08X}, got 0x{received_crc:08X}")
    return header, payload


class FrameReader:
    """Stateful streaming reader parsing messages from TCP/TLS chunks."""

    def __init__(self, max_payload_len: int = 10 * 1024 * 1024) -> None:
        self._buf = bytearray()
        self.max_payload_len = max_payload_len

    def feed(self, chunk: bytes) -> None:
        self._buf.extend(chunk)

    def next_frame(self) -> tuple[Header, bytes] | None:
        if len(self._buf) < HEADER_SIZE:
            return None
        header = Header.unpack(bytes(self._buf[:HEADER_SIZE]))
        if header.payload_len > self.max_payload_len:
            raise ValueError(f"Payload length {header.payload_len} exceeds max {self.max_payload_len}")
        expected_total = HEADER_SIZE + header.payload_len + TRAILER_SIZE
        if len(self._buf) < expected_total:
            return None
        raw_frame = bytes(self._buf[:expected_total])
        del self._buf[:expected_total]
        return unframe_message(raw_frame)


# --- Helper Encodings ---

def encode_utf8(s: str) -> bytes:
    encoded = s.encode("utf-8")
    if len(encoded) > 65535:
        raise ValueError("String too long for u16 length prefix")
    return struct.pack("<H", len(encoded)) + encoded


def decode_utf8(data: bytes, offset: int = 0) -> tuple[str, int]:
    if offset + 2 > len(data):
        raise ValueError("Not enough data for string length")
    slen = struct.unpack_from("<H", data, offset)[0]
    start = offset + 2
    end = start + slen
    if end > len(data):
        raise ValueError("String extends beyond buffer")
    return data[start:end].decode("utf-8"), end


# --- Payload Definitions ---

@dataclass
class HelloPayload:
    agent_id: str
    hostname: str
    os_version: str
    capabilities: list[str]

    def pack(self) -> bytes:
        buf = bytearray()
        buf.extend(encode_utf8(self.agent_id))
        buf.extend(encode_utf8(self.hostname))
        buf.extend(encode_utf8(self.os_version))
        buf.extend(struct.pack("<H", len(self.capabilities)))
        for cap in self.capabilities:
            buf.extend(encode_utf8(cap))
        return bytes(buf)

    @classmethod
    def unpack(cls, data: bytes) -> HelloPayload:
        agent_id, off = decode_utf8(data, 0)
        hostname, off = decode_utf8(data, off)
        os_version, off = decode_utf8(data, off)
        caps_count = struct.unpack_from("<H", data, off)[0]
        off += 2
        caps = []
        for _ in range(caps_count):
            cap, off = decode_utf8(data, off)
            caps.append(cap)
        return cls(agent_id=agent_id, hostname=hostname, os_version=os_version, capabilities=caps)


@dataclass
class HelloAckPayload:
    session_id: str
    heartbeat_interval_ms: int

    def pack(self) -> bytes:
        buf = bytearray()
        buf.extend(encode_utf8(self.session_id))
        buf.extend(struct.pack("<I", self.heartbeat_interval_ms))
        return bytes(buf)

    @classmethod
    def unpack(cls, data: bytes) -> HelloAckPayload:
        session_id, off = decode_utf8(data, 0)
        heartbeat_ms = struct.unpack_from("<I", data, off)[0]
        return cls(session_id=session_id, heartbeat_interval_ms=heartbeat_ms)


@dataclass
class AuthPayload:
    token_hash: bytes

    def pack(self) -> bytes:
        return struct.pack("<H", len(self.token_hash)) + self.token_hash

    @classmethod
    def unpack(cls, data: bytes) -> AuthPayload:
        tlen = struct.unpack_from("<H", data, 0)[0]
        token_hash = data[2 : 2 + tlen]
        return cls(token_hash=token_hash)


@dataclass
class AuthAckPayload:
    success: bool
    policies_version: int

    def pack(self) -> bytes:
        return struct.pack("<?I", self.success, self.policies_version)

    @classmethod
    def unpack(cls, data: bytes) -> AuthAckPayload:
        success, policies_version = struct.unpack_from("<?I", data, 0)
        return cls(success=success, policies_version=policies_version)


@dataclass
class HeartbeatPayload:
    timestamp_us: int

    def pack(self) -> bytes:
        return struct.pack("<Q", self.timestamp_us)

    @classmethod
    def unpack(cls, data: bytes) -> HeartbeatPayload:
        ts = struct.unpack_from("<Q", data, 0)[0]
        return cls(timestamp_us=ts)


@dataclass
class ItemDataUpdate:
    """Individual item measurement in a data stream payload."""
    item_id: int
    opc_item_path: str
    status: int
    value_type: int
    quality: int
    timestamp_us: int
    value: bytes
    error_code: int = 0

    def pack(self) -> bytes:
        buf = bytearray()
        buf.extend(struct.pack("<I", self.item_id))
        buf.extend(encode_utf8(self.opc_item_path))
        buf.extend(struct.pack("<B", self.status))
        buf.extend(struct.pack("<B", self.value_type))
        buf.extend(struct.pack("<H", self.quality))
        buf.extend(struct.pack("<Q", self.timestamp_us))
        buf.extend(struct.pack("<I", len(self.value)))
        buf.extend(self.value)
        buf.extend(struct.pack("<I", self.error_code))
        return bytes(buf)

    @classmethod
    def unpack(cls, data: bytes, offset: int = 0) -> tuple[ItemDataUpdate, int]:
        item_id = struct.unpack_from("<I", data, offset)[0]
        opc_item_path, off = decode_utf8(data, offset + 4)
        status = struct.unpack_from("<B", data, off)[0]
        value_type = struct.unpack_from("<B", data, off + 1)[0]
        quality = struct.unpack_from("<H", data, off + 2)[0]
        timestamp_us = struct.unpack_from("<Q", data, off + 4)[0]
        vlen = struct.unpack_from("<I", data, off + 12)[0]
        vstart = off + 16
        value = data[vstart : vstart + vlen]
        error_code = struct.unpack_from("<I", data, vstart + vlen)[0]
        next_off = vstart + vlen + 4
        return cls(
            item_id=item_id,
            opc_item_path=opc_item_path,
            status=status,
            value_type=value_type,
            quality=quality,
            timestamp_us=timestamp_us,
            value=value,
            error_code=error_code,
        ), next_off

    def to_item_sample(self, session_id: str, sequence: int, source_id: str = "default_source") -> ItemSample:
        """Convert to ItemSample for cache ingestion."""
        val, _ = decode_value(self.value_type, self.value)
        return ItemSample(
            item_id=self.item_id,
            opc_item_path=self.opc_item_path,
            value=val,
            value_bytes=self.value,
            value_type=self.value_type,
            quality=self.quality,
            timestamp_us=self.timestamp_us,
            status=self.status,
            error_code=self.error_code,
            source_id=source_id,
            session_id=session_id,
            sequence=sequence,
        )


@dataclass
class DataStreamPayload:
    """Telemetry data stream update conveying a batch of measurements under a monotonic sequence."""
    session_id: str
    sequence: int
    source_id: str = "default_source"
    items: list[ItemDataUpdate] = field(default_factory=list)

    def pack(self) -> bytes:
        buf = bytearray()
        buf.extend(encode_utf8(self.source_id))
        buf.extend(encode_utf8(self.session_id))
        buf.extend(struct.pack("<Q", self.sequence))
        buf.extend(struct.pack("<H", len(self.items)))
        for item in self.items:
            buf.extend(item.pack())
        return bytes(buf)

    @classmethod
    def unpack(cls, data: bytes) -> DataStreamPayload:
        source_id, off = decode_utf8(data, 0)
        session_id, off = decode_utf8(data, off)
        sequence = struct.unpack_from("<Q", data, off)[0]
        count = struct.unpack_from("<H", data, off + 8)[0]
        off += 10
        items = []
        for _ in range(count):
            item, off = ItemDataUpdate.unpack(data, off)
            items.append(item)
        return cls(session_id=session_id, sequence=sequence, source_id=source_id, items=items)