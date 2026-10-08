from __future__ import annotations

import json
import pytest

from opc_da_bridge.bridge.json_protocol import MIRROR_ITEMS, decode_request, encode_message, record_from_cache
from opc_da_bridge.bridge.server import CacheBridgeServer
from opc_da_bridge.core.cache import BoundedLatestValueCache
from opc_da_bridge.core.model import ItemSample, OPC_QUALITY_BAD_NOT_CONNECTED, OPC_QUALITY_GOOD, ValueType


def _sample(cache: BoundedLatestValueCache, path: str, value: float, seq: int, quality: int = OPC_QUALITY_GOOD):
    cache.bind_active_session("source", "session")
    sample = ItemSample.create(1, path, value, ValueType.F64, quality=quality, timestamp_us=1791475200123456,
                               source_id="source", session_id="session", sequence=seq)
    cache.put_sample(sample)


def test_bridge_snapshot_preserves_value_type_quality_timestamp_and_sequence():
    cache = BoundedLatestValueCache()
    _sample(cache, MIRROR_ITEMS["OPCBridge.Mirror.Lab.Temperature"][0], 23.5, 42)
    snapshot = cache.snapshot_batch([MIRROR_ITEMS["OPCBridge.Mirror.Lab.Temperature"][0]])
    item = snapshot[MIRROR_ITEMS["OPCBridge.Mirror.Lab.Temperature"][0]]
    record = record_from_cache("OPCBridge.Mirror.Lab.Temperature", item)
    assert record["value"] == 23.5
    assert record["value_type"] == "float"
    assert record["quality"] == "Good"
    assert record["quality_code"] == OPC_QUALITY_GOOD
    assert record["timestamp_utc"] == "2026-10-08T16:00:00.123Z"
    assert record["source_connected"] is True and record["is_stale"] is False
    assert record["sequence"] == 42


def test_restored_bad_or_disconnected_quality_never_becomes_good():
    cache = BoundedLatestValueCache()
    _sample(cache, "x", 1.0, 1, OPC_QUALITY_BAD_NOT_CONNECTED)
    item = cache.snapshot_batch(["x"])["x"]
    rec = record_from_cache("x", item)
    assert rec["quality"] != "Good"
    assert rec["source_connected"] is True
    assert rec["quality_code"] == OPC_QUALITY_BAD_NOT_CONNECTED


def test_protocol_is_read_only_and_rejects_invalid_requests():
    assert decode_request(b'{"op":"snapshot"}\n') == {"op": "snapshot"}
    with pytest.raises(PermissionError):
        decode_request(b'{"op":"write","value":1}\n')
    with pytest.raises(PermissionError):
        encode_message({"op": "write"})


def test_mirror_namespace_and_loopback_only():
    assert set(MIRROR_ITEMS) == {"OPCBridge.Mirror.Lab.Temperature", "OPCBridge.Mirror.Lab.Pressure"}
    cache = BoundedLatestValueCache()
    with pytest.raises(ValueError, match="loopback"):
        CacheBridgeServer(cache, host="0.0.0.0")


def test_json_line_encoding_is_finite_and_utf8():
    payload = encode_message({"op": "snapshot", "items": [{"item_id": "OPCBridge.Mirror.Lab.Pressure", "value": 100.25}]})
    assert payload.endswith(b"\n")
    assert json.loads(payload)["items"][0]["value"] == 100.25
