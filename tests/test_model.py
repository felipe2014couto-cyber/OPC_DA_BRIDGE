"""Unit tests for core data model, value types, and quality words."""
from __future__ import annotations

import struct
from datetime import datetime, timezone
import pytest

from opc_da_bridge.core.model import (
    ItemSample,
    ItemStatus,
    ValueType,
    decode_value,
    encode_value,
    format_opc_timestamp,
    format_quality_text,
    is_quality_bad,
    is_quality_good,
    is_quality_uncertain,
    timestamp_us_to_datetime,
    utc_now_us,
    OPC_QUALITY_BAD,
    OPC_QUALITY_BAD_COMM_FAILURE,
    OPC_QUALITY_BAD_LAST_KNOWN_VALUE,
    OPC_QUALITY_BAD_NOT_CONNECTED,
    OPC_QUALITY_GOOD,
    OPC_QUALITY_GOOD_LOCAL_OVERRIDE,
    OPC_QUALITY_UNCERTAIN,
    OPC_QUALITY_UNCERTAIN_LAST_USABLE,
)


def test_value_encoding_decoding_roundtrip():
    # I16
    raw_i16 = encode_value(ValueType.I16, -1234)
    val_i16, type_i16 = decode_value(ValueType.I16, raw_i16)
    assert val_i16 == -1234
    assert type_i16 == "I16"

    # I32
    raw_i32 = encode_value(ValueType.I32, 987654)
    val_i32, type_i32 = decode_value(ValueType.I32, raw_i32)
    assert val_i32 == 987654
    assert type_i32 == "I32"

    # F32
    raw_f32 = encode_value(ValueType.F32, 123.456)
    val_f32, type_f32 = decode_value(ValueType.F32, raw_f32)
    assert abs(val_f32 - 123.456) < 0.001
    assert type_f32 == "F32"

    # F64
    raw_f64 = encode_value(ValueType.F64, 98765.4321)
    val_f64, type_f64 = decode_value(ValueType.F64, raw_f64)
    assert abs(val_f64 - 98765.4321) < 0.000001
    assert type_f64 == "F64"

    # BOOL
    raw_bool_true = encode_value(ValueType.BOOL, True)
    val_bool_true, type_bool = decode_value(ValueType.BOOL, raw_bool_true)
    assert val_bool_true is True
    assert type_bool == "BOOL"

    raw_bool_false = encode_value(ValueType.BOOL, False)
    val_bool_false, _ = decode_value(ValueType.BOOL, raw_bool_false)
    assert val_bool_false is False

    # STRING
    raw_str = encode_value(ValueType.STRING, "Turbine_Active_42")
    val_str, type_str = decode_value(ValueType.STRING, raw_str)
    assert val_str == "Turbine_Active_42"
    assert type_str == "STRING"

    # BLOB
    raw_blob = encode_value(ValueType.BLOB, b"\x01\x02\x03\x04")
    val_blob, type_blob = decode_value(ValueType.BLOB, raw_blob)
    assert val_blob == b"\x01\x02\x03\x04"
    assert type_blob == "BLOB"


def test_quality_word_classification():
    # Good
    assert is_quality_good(OPC_QUALITY_GOOD) is True
    assert is_quality_bad(OPC_QUALITY_GOOD) is False
    assert is_quality_uncertain(OPC_QUALITY_GOOD) is False
    assert format_quality_text(OPC_QUALITY_GOOD) == "Good"
    assert format_quality_text(OPC_QUALITY_GOOD_LOCAL_OVERRIDE) == "Good [Local Override]"

    # Uncertain
    assert is_quality_uncertain(OPC_QUALITY_UNCERTAIN) is True
    assert is_quality_good(OPC_QUALITY_UNCERTAIN) is False
    assert is_quality_uncertain(OPC_QUALITY_UNCERTAIN_LAST_USABLE) is True
    assert format_quality_text(OPC_QUALITY_UNCERTAIN_LAST_USABLE) == "Uncertain [Last Usable Value]"

    # Bad
    assert is_quality_bad(OPC_QUALITY_BAD) is True
    assert is_quality_bad(OPC_QUALITY_BAD_NOT_CONNECTED) is True
    assert is_quality_bad(OPC_QUALITY_BAD_COMM_FAILURE) is True
    assert is_quality_bad(OPC_QUALITY_BAD_LAST_KNOWN_VALUE) is True
    assert format_quality_text(OPC_QUALITY_BAD_NOT_CONNECTED) == "Bad [Not Connected]"
    assert format_quality_text(OPC_QUALITY_BAD_COMM_FAILURE) == "Bad [Communication Failure]"
    assert format_quality_text(OPC_QUALITY_BAD_LAST_KNOWN_VALUE) == "Bad [Last Known Value]"


def test_timestamp_utilities():
    now_us = utc_now_us()
    assert now_us > 1_600_000_000_000_000  # reasonable recent epoch

    dt = timestamp_us_to_datetime(now_us)
    assert dt.tzinfo == timezone.utc

    formatted = format_opc_timestamp(now_us)
    assert formatted is not None
    assert "/" in formatted and ":" in formatted and "." in formatted

    # Invalid timestamp handling
    assert format_opc_timestamp(0) is None
    assert format_opc_timestamp(-100) is None


def test_item_sample_creation():
    sample = ItemSample.create(
        item_id=10,
        opc_item_path="Plant.Section.TagA",
        value=150.5,
        value_type=ValueType.F64,
        quality=OPC_QUALITY_GOOD,
        session_id="sess_abc",
        sequence=42,
    )
    assert sample.item_id == 10
    assert sample.opc_item_path == "Plant.Section.TagA"
    assert sample.value == 150.5
    assert len(sample.value_bytes) == 8
    assert sample.quality == OPC_QUALITY_GOOD
    assert sample.session_id == "sess_abc"
    assert sample.sequence == 42
    assert sample.timestamp_us > 0
