"""Unit tests for binary framing, CRC verification, and message serialization."""
from __future__ import annotations

import struct
import pytest

from opc_da_bridge.protocol.messages import (
    MAGIC,
    VERSION,
    VERSION_V1,
    AuthAckPayload,
    AuthPayload,
    DataStreamPayload,
    FrameReader,
    Header,
    HeartbeatPayload,
    HelloAckPayload,
    HelloPayload,
    ItemDataUpdate,
    MsgType,
    frame_message,
    unframe_message,
)


def test_header_pack_unpack_and_versioning():
    hdr = Header(magic=MAGIC, version=VERSION, msg_type=MsgType.HELLO, flags=0, seq=10, payload_len=50)
    packed = hdr.pack()
    assert len(packed) == 16

    unpacked = Header.unpack(packed)
    assert unpacked.magic == MAGIC
    assert unpacked.version == VERSION
    assert unpacked.msg_type == MsgType.HELLO
    assert unpacked.seq == 10
    assert unpacked.payload_len == 50

    # Backwards compatibility with v1
    hdr_v1 = Header(magic=MAGIC, version=VERSION_V1, msg_type=MsgType.HELLO, flags=0, seq=1, payload_len=10)
    unpacked_v1 = Header.unpack(hdr_v1.pack(), allow_v1=True)
    assert unpacked_v1.version == VERSION_V1

    # Invalid magic
    bad_magic = struct.pack("<HHHHII", 0x1234, VERSION, 1, 0, 0, 0)
    with pytest.raises(ValueError, match="Invalid magic"):
        Header.unpack(bad_magic)

    # Invalid version
    bad_version = struct.pack("<HHHHII", MAGIC, 0x0099, 1, 0, 0, 0)
    with pytest.raises(ValueError, match="Unsupported protocol version"):
        Header.unpack(bad_version, allow_v1=False)


def test_frame_and_unframe_crc_integrity():
    payload = b"Sample industrial payload data 12345"
    framed = frame_message(MsgType.DATA_STREAM, seq=42, payload=payload)
    assert len(framed) == 16 + len(payload) + 4

    hdr, decoded_payload = unframe_message(framed)
    assert hdr.msg_type == MsgType.DATA_STREAM
    assert hdr.seq == 42
    assert decoded_payload == payload

    # Corrupt CRC
    corrupted = bytearray(framed)
    corrupted[-1] ^= 0xFF
    with pytest.raises(ValueError, match="CRC mismatch"):
        unframe_message(bytes(corrupted))

    # Truncated message
    with pytest.raises(ValueError, match="Incomplete message"):
        unframe_message(framed[:-5])


def test_handshake_payloads():
    # Hello
    hello = HelloPayload(agent_id="agt1", hostname="host1", os_version="win10", capabilities=["capA", "capB"])
    h_bytes = hello.pack()
    hello_decoded = HelloPayload.unpack(h_bytes)
    assert hello_decoded.agent_id == "agt1"
    assert hello_decoded.hostname == "host1"
    assert hello_decoded.capabilities == ["capA", "capB"]

    # HelloAck
    ack = HelloAckPayload(session_id="sess_123", heartbeat_interval_ms=3000)
    ack_decoded = HelloAckPayload.unpack(ack.pack())
    assert ack_decoded.session_id == "sess_123"
    assert ack_decoded.heartbeat_interval_ms == 3000

    # Auth
    auth = AuthPayload(token_hash=b"\x01\x02\x03\x04")
    auth_decoded = AuthPayload.unpack(auth.pack())
    assert auth_decoded.token_hash == b"\x01\x02\x03\x04"

    # AuthAck
    auth_ack = AuthAckPayload(success=True, policies_version=2)
    auth_ack_dec = AuthAckPayload.unpack(auth_ack.pack())
    assert auth_ack_dec.success is True
    assert auth_ack_dec.policies_version == 2

    # Heartbeat
    hb = HeartbeatPayload(timestamp_us=123456789)
    hb_dec = HeartbeatPayload.unpack(hb.pack())
    assert hb_dec.timestamp_us == 123456789


def test_data_stream_payload_roundtrip():
    items = [
        ItemDataUpdate(
            item_id=1,
            opc_item_path="Plant.Pressure",
            status=0,
            value_type=3,  # F64
            quality=0xC0,
            timestamp_us=1700000000000,
            value=struct.pack("<d", 101.325),
            error_code=0,
        ),
        ItemDataUpdate(
            item_id=2,
            opc_item_path="Plant.ValveOpen",
            status=0,
            value_type=4,  # BOOL
            quality=0xC0,
            timestamp_us=1700000000000,
            value=b"\x01",
            error_code=0,
        ),
    ]

    stream = DataStreamPayload(session_id="sess_xyz", sequence=105, source_id="source_alpha", items=items)
    packed = stream.pack()
    unpacked = DataStreamPayload.unpack(packed)

    assert unpacked.source_id == "source_alpha"
    assert unpacked.session_id == "sess_xyz"
    assert unpacked.sequence == 105
    assert len(unpacked.items) == 2
    assert unpacked.items[0].opc_item_path == "Plant.Pressure"
    assert unpacked.items[0].value == struct.pack("<d", 101.325)
    assert unpacked.items[1].opc_item_path == "Plant.ValveOpen"
    assert unpacked.items[1].value == b"\x01"


def test_frame_reader_fragmentation_and_concatenation():
    reader = FrameReader()

    f1 = frame_message(MsgType.HEARTBEAT, seq=1, payload=HeartbeatPayload(100).pack())
    f2 = frame_message(MsgType.HEARTBEAT, seq=2, payload=HeartbeatPayload(200).pack())

    # Feed split packet across chunks
    reader.feed(f1[:7])
    assert reader.next_frame() is None

    reader.feed(f1[7:])
    frame1 = reader.next_frame()
    assert frame1 is not None
    assert frame1[0].seq == 1
    assert reader.next_frame() is None

    # Feed two concatenated packets in one chunk
    reader.feed(f1 + f2)
    fr1 = reader.next_frame()
    assert fr1 is not None
    assert fr1[0].seq == 1
    fr2 = reader.next_frame()
    assert fr2 is not None
    assert fr2[0].seq == 2
    assert reader.next_frame() is None
