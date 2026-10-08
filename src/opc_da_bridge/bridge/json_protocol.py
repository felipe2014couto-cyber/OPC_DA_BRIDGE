"""Versioned read-only JSON contract for the local cache-to-Mirror link."""
from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from typing import Any

from opc_da_bridge.core.model import (
    OPC_QUALITY_GOOD,
    CachedItemValue,
    VALUE_TYPE_NAMES,
)

PROTOCOL_VERSION = 1
MIRROR_ITEMS = {
    "OPCBridge.Mirror.Lab.Temperature": ("Simulated.Furnace.Temperature", "float"),
    "OPCBridge.Mirror.Lab.Pressure": ("Simulated.Line.Pressure", "float"),
}


def utc_iso_from_us(timestamp_us: int) -> str:
    seconds, micros = divmod(timestamp_us, 1_000_000)
    return (
        datetime.fromtimestamp(seconds, timezone.utc)
        .replace(microsecond=micros)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def record_from_cache(item_id: str, item: CachedItemValue) -> dict[str, Any]:
    """Build a JSON-safe immutable-by-serialization snapshot; never upgrade quality."""
    connected = not item.disconnected
    stale = item.stale or not connected
    quality = item.quality
    if (not connected or stale) and (quality & 0xC0) == 0xC0:
        quality = 0x18 if connected else 0x08
    value_type = VALUE_TYPE_NAMES.get(int(item.value_type), f"TYPE_{item.value_type}").lower()
    if value_type == "f64":
        value_type = "float"
    value = item.value
    if isinstance(value, (bytes, bytearray, memoryview)):
        value = base64.b64encode(bytes(value)).decode("ascii")
    return {
        "protocol_version": PROTOCOL_VERSION,
        "item_id": item_id,
        "value": value,
        "value_type": value_type,
        "quality": (
            "Good"
            if (quality & 0xC0) == OPC_QUALITY_GOOD and connected and not stale
            else f"0x{quality:04X}"
        ),
        "quality_code": quality,
        "timestamp_utc": utc_iso_from_us(item.source_timestamp_us),
        "source_connected": connected,
        "is_stale": stale,
        "sequence": item.sequence,
    }


def encode_message(message: dict[str, Any]) -> bytes:
    """Encode one UTF-8 JSON line. Only snapshot reads are supported."""
    if message.get("op") not in ("snapshot", "read"):
        raise PermissionError("local Mirror bridge is read-only")
    return (json.dumps(message, separators=(",", ":"), ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def decode_request(line: bytes) -> dict[str, Any]:
    request = json.loads(line.decode("utf-8"))
    if not isinstance(request, dict) or request.get("op") not in ("snapshot", "read"):
        raise PermissionError("local Mirror bridge is read-only")
    return request
