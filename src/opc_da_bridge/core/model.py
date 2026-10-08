"""Platform-neutral OPC DA data model, value encoding, and quality definitions.

Follows OPC DA 2.05 standard quality definitions and provides safe, pure-Python
typing and serialization with zero Windows/COM dependencies.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import IntEnum
import struct
import time
from typing import Any

# OPC DA 2.05 Major Quality masks and values
OPC_QUALITY_MASK = 0xC0
OPC_STATUS_MASK = 0xFC
OPC_LIMIT_MASK = 0x03

OPC_QUALITY_BAD = 0x00
OPC_QUALITY_UNCERTAIN = 0x40
OPC_QUALITY_GOOD = 0xC0

# OPC DA 2.05 Bad sub-status codes
OPC_QUALITY_BAD_NON_SPECIFIC = 0x00
OPC_QUALITY_BAD_CONFIG_ERROR = 0x04
OPC_QUALITY_BAD_NOT_CONNECTED = 0x08
OPC_QUALITY_BAD_DEVICE_FAILURE = 0x0C
OPC_QUALITY_BAD_SENSOR_FAILURE = 0x10
OPC_QUALITY_BAD_LAST_KNOWN_VALUE = 0x14
OPC_QUALITY_BAD_COMM_FAILURE = 0x18
OPC_QUALITY_BAD_OUT_OF_SERVICE = 0x1C

# OPC DA 2.05 Uncertain sub-status codes
OPC_QUALITY_UNCERTAIN_NON_SPECIFIC = 0x40
OPC_QUALITY_UNCERTAIN_LAST_USABLE = 0x44
OPC_QUALITY_UNCERTAIN_SENSOR_NOT_ACCURATE = 0x50
OPC_QUALITY_UNCERTAIN_EU_EXCEEDED = 0x54
OPC_QUALITY_UNCERTAIN_SUB_NORMAL = 0x58

# OPC DA 2.05 Good sub-status codes
OPC_QUALITY_GOOD_NON_SPECIFIC = 0xC0
OPC_QUALITY_GOOD_LOCAL_OVERRIDE = 0xD8

# Limit status
OPC_LIMIT_OK = 0x00
OPC_LIMIT_LOW = 0x01
OPC_LIMIT_HIGH = 0x02
OPC_LIMIT_CONST = 0x03


class ItemStatus(IntEnum):
    """Status of an item read or cached entry."""
    OK = 0
    BAD_QUALITY = 1
    NOT_FOUND = 2
    TIMEOUT = 3
    ERROR = 4
    STALE = 5
    DISCONNECTED = 6


class ValueType(IntEnum):
    """Supported OPC DA canonical data types."""
    I16 = 0
    I32 = 1
    F32 = 2
    F64 = 3
    BOOL = 4
    STRING = 5
    BLOB = 6


VALUE_TYPE_NAMES: dict[int, str] = {
    ValueType.I16: "I16",
    ValueType.I32: "I32",
    ValueType.F32: "F32",
    ValueType.F64: "F64",
    ValueType.BOOL: "BOOL",
    ValueType.STRING: "STRING",
    ValueType.BLOB: "BLOB",
}


def utc_now_us() -> int:
    """Return current UTC timestamp in microseconds since Unix epoch."""
    return int(time.time() * 1_000_000)


def timestamp_us_to_datetime(timestamp_us: int) -> datetime:
    """Convert microsecond timestamp to timezone-aware UTC datetime."""
    return datetime.fromtimestamp(timestamp_us / 1_000_000, tz=timezone.utc)


def format_opc_timestamp(timestamp_us: int) -> str | None:
    """Format microsecond timestamp into OPC standard dd/MM/yyyy HH:mm:ss.SSS format."""
    if timestamp_us <= 0:
        return None
    try:
        dt = timestamp_us_to_datetime(timestamp_us)
        ms = (timestamp_us % 1_000_000) // 1000
        return dt.strftime("%d/%m/%Y %H:%M:%S") + f".{ms:03d}"
    except Exception:
        return None


def is_quality_good(quality: int) -> bool:
    """Check if 16-bit quality word is Good (0xC0)."""
    return (quality & OPC_QUALITY_MASK) == OPC_QUALITY_GOOD


def is_quality_uncertain(quality: int) -> bool:
    """Check if 16-bit quality word is Uncertain (0x40)."""
    return (quality & OPC_QUALITY_MASK) == OPC_QUALITY_UNCERTAIN


def is_quality_bad(quality: int) -> bool:
    """Check if 16-bit quality word is Bad (0x00)."""
    return (quality & OPC_QUALITY_MASK) == OPC_QUALITY_BAD


def format_quality_text(quality: int) -> str:
    """Format 16-bit quality word into readable text."""
    major = quality & OPC_QUALITY_MASK
    if major == OPC_QUALITY_GOOD:
        if quality == OPC_QUALITY_GOOD_LOCAL_OVERRIDE:
            return "Good [Local Override]"
        return "Good"
    if major == OPC_QUALITY_UNCERTAIN:
        sub = quality & OPC_STATUS_MASK
        if sub == OPC_QUALITY_UNCERTAIN_LAST_USABLE:
            return "Uncertain [Last Usable Value]"
        return "Uncertain"
    sub = quality & OPC_STATUS_MASK
    if sub == OPC_QUALITY_BAD_NOT_CONNECTED:
        return "Bad [Not Connected]"
    if sub == OPC_QUALITY_BAD_COMM_FAILURE:
        return "Bad [Communication Failure]"
    if sub == OPC_QUALITY_BAD_LAST_KNOWN_VALUE:
        return "Bad [Last Known Value]"
    if sub == OPC_QUALITY_BAD_CONFIG_ERROR:
        return "Bad [Configuration Error]"
    if sub == OPC_QUALITY_BAD_OUT_OF_SERVICE:
        return "Bad [Out Of Service]"
    return "Bad"


def encode_value(value_type: int, value: Any) -> bytes:
    """Encode a typed Python value into canonical little-endian byte representation."""
    if value is None:
        return b""
    if value_type == ValueType.I16:
        return struct.pack("<h", int(value))
    elif value_type == ValueType.I32:
        return struct.pack("<i", int(value))
    elif value_type == ValueType.F32:
        return struct.pack("<f", float(value))
    elif value_type == ValueType.F64:
        return struct.pack("<d", float(value))
    elif value_type == ValueType.BOOL:
        return struct.pack("<?", bool(value))
    elif value_type == ValueType.STRING:
        if isinstance(value, bytes):
            return value
        return str(value).encode("utf-8")
    elif value_type == ValueType.BLOB:
        if isinstance(value, bytes):
            return value
        if isinstance(value, str):
            return bytes.fromhex(value)
        return bytes(value)
    raise ValueError(f"Unsupported value type: {value_type}")


def decode_value(value_type: int, raw_bytes: bytes) -> tuple[Any, str]:
    """Safely decode raw bytes according to ValueType."""
    type_name = VALUE_TYPE_NAMES.get(value_type, f"TYPE_{value_type}")
    if not raw_bytes:
        return None, type_name
    try:
        if value_type == ValueType.I16:
            if len(raw_bytes) >= 2:
                return struct.unpack("<h", raw_bytes[:2])[0], type_name
        elif value_type == ValueType.I32:
            if len(raw_bytes) >= 4:
                return struct.unpack("<i", raw_bytes[:4])[0], type_name
        elif value_type == ValueType.F32:
            if len(raw_bytes) >= 4:
                return round(struct.unpack("<f", raw_bytes[:4])[0], 6), type_name
        elif value_type == ValueType.F64:
            if len(raw_bytes) >= 8:
                return struct.unpack("<d", raw_bytes[:8])[0], type_name
        elif value_type == ValueType.BOOL:
            if len(raw_bytes) >= 1:
                return bool(struct.unpack("<?", raw_bytes[:1])[0]), type_name
        elif value_type == ValueType.STRING:
            return raw_bytes.decode("utf-8", errors="replace"), type_name
        elif value_type == ValueType.BLOB:
            return raw_bytes, type_name
    except Exception:
        pass
    return None, type_name


@dataclass
class ItemRef:
    """Reference to an item by ID and OPC path."""
    item_id: int
    opc_item_path: str
    requested_source: int = 0  # 0=Device, 1=Cache


@dataclass
class ItemSample:
    """Individual measurement produced at the source."""
    item_id: int
    opc_item_path: str
    value: Any
    value_bytes: bytes
    value_type: int
    quality: int
    timestamp_us: int
    status: int = int(ItemStatus.OK)
    error_code: int = 0
    source_id: str = "default_source"
    session_id: str = ""
    sequence: int = 0

    @classmethod
    def create(
        cls,
        item_id: int,
        opc_item_path: str,
        value: Any,
        value_type: int,
        quality: int = OPC_QUALITY_GOOD,
        timestamp_us: int | None = None,
        status: int = int(ItemStatus.OK),
        error_code: int = 0,
        source_id: str = "default_source",
        session_id: str = "",
        sequence: int = 0,
    ) -> ItemSample:
        """Helper to create a fully initialized ItemSample."""
        ts = timestamp_us if timestamp_us is not None else utc_now_us()
        raw = encode_value(value_type, value)
        return cls(
            item_id=item_id,
            opc_item_path=opc_item_path,
            value=value,
            value_bytes=raw,
            value_type=value_type,
            quality=quality,
            timestamp_us=ts,
            status=status,
            error_code=error_code,
            source_id=source_id,
            session_id=session_id,
            sequence=sequence,
        )


@dataclass
class CachedItemValue:
    """Item state as held within the bounded latest-value cache."""
    item_id: int
    opc_item_path: str
    value: Any
    raw_value_bytes: bytes
    value_type: int
    quality: int
    source_timestamp_us: int
    last_updated_at: float  # local monotonic timestamp
    source_id: str = "default_source"
    session_id: str = ""
    sequence: int = 0
    stale: bool = False
    disconnected: bool = False
    status: int = int(ItemStatus.OK)
    error_code: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Serialize for inspection or safe snapshot (raw_value_bytes in hex)."""
        return {
            "item_id": self.item_id,
            "opc_item_path": self.opc_item_path,
            "source_id": self.source_id,
            "value": self.value,
            "raw_value_hex": self.raw_value_bytes.hex(),
            "value_type": int(self.value_type),
            "value_type_name": VALUE_TYPE_NAMES.get(self.value_type, "UNKNOWN"),
            "quality": self.quality,
            "quality_text": format_quality_text(self.quality),
            "source_timestamp_us": self.source_timestamp_us,
            "source_timestamp_iso": timestamp_us_to_datetime(self.source_timestamp_us).isoformat() if self.source_timestamp_us > 0 else None,
            "session_id": self.session_id,
            "sequence": self.sequence,
            "stale": self.stale,
            "disconnected": self.disconnected,
            "status": int(self.status),
            "error_code": self.error_code,
        }
