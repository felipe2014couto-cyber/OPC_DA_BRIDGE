"""Platform-neutral contracts and types for OPC DA Mirror.

Defines canonical OPC DA constants, VARTYPE mappings, Windows FILETIME conversions,
and immutable data structures for client interaction. Zero COM or Windows dependencies.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import struct
import time
from typing import Any

from opc_da_bridge.core.model import ValueType, utc_now_us
from opc_da_bridge.mirror.hresult import OPC_E_BADRIGHTS, S_FALSE, S_OK

# --- OPC DA Server Status Enumeration ---
OPC_STATUS_RUNNING: int = 1
OPC_STATUS_FAILED: int = 2
OPC_STATUS_NOCONFIG: int = 3
OPC_STATUS_SUSPENDED: int = 4
OPC_STATUS_TEST: int = 5
OPC_STATUS_COMM_FAULT: int = 6

# --- OPC DA Access Rights ---
OPC_READABLE: int = 1
OPC_WRITEABLE: int = 2

# --- OPC DA Data Sources ---
OPC_DS_CACHE: int = 1
OPC_DS_DEVICE: int = 2

# --- OLE Automation VARTYPE Constants ---
VT_EMPTY: int = 0
VT_NULL: int = 1
VT_I2: int = 2
VT_I4: int = 3
VT_R4: int = 4
VT_R8: int = 5
VT_BSTR: int = 8
VT_BOOL: int = 11
VT_VARIANT: int = 12
VT_UI1: int = 17
VT_ARRAY: int = 0x2000

# Mapping from canonical ValueType to OLE Automation VARTYPE
VALUETYPE_TO_VARTYPE: dict[int, int] = {
    int(ValueType.I16): VT_I2,
    int(ValueType.I32): VT_I4,
    int(ValueType.F32): VT_R4,
    int(ValueType.F64): VT_R8,
    int(ValueType.BOOL): VT_BOOL,
    int(ValueType.STRING): VT_BSTR,
    int(ValueType.BLOB): VT_ARRAY | VT_UI1,  # SafeArray of Unsigned Bytes (0x2011)
}


def valuetype_to_vartype(value_type: ValueType | int) -> int:
    """Map canonical ValueType to COM VARTYPE representation."""
    vt = int(value_type)
    return VALUETYPE_TO_VARTYPE.get(vt, VT_EMPTY)


# --- Windows FILETIME Conversion Utilities ---
# Windows FILETIME represents the number of 100-nanosecond intervals since January 1, 1601 (UTC).
# The difference between 1601-01-01 and 1970-01-01 (Unix Epoch) is 11,644,473,600 seconds.
# In 100-nanosecond intervals: 116,444,736,000,000,000 (0x019DB1DED53E8000).
EPOCH_DIFF_FILETIME_TICKS: int = 116444736000000000
MAX_FILETIME_TICKS: int = (1 << 64) - 1

# Representable UTC timestamp bounds in microseconds (since Unix Epoch 1970-01-01):
# MIN corresponds to FILETIME ticks = 0 (1601-01-01 00:00:00 UTC)
MIN_TIMESTAMP_US: int = -(EPOCH_DIFF_FILETIME_TICKS // 10)  # -11,644,473,600,000,000 us
# MAX corresponds to FILETIME ticks = 2^64 - 1
MAX_TIMESTAMP_US: int = (MAX_FILETIME_TICKS - EPOCH_DIFF_FILETIME_TICKS) // 10  # 1,728,229,671,370,955,161 us


def utc_us_to_filetime(timestamp_us: int) -> tuple[int, int]:
    """Convert microsecond UTC timestamp (Unix epoch based) to Windows FILETIME (dwLowDateTime, dwHighDateTime).

    Preserves the exact source timestamp without fabricating readings.
    Unix epoch (timestamp_us = 0) produces FILETIME corresponding to 1970-01-01 00:00:00 UTC (116444736000000000 ticks).
    Returns:
        tuple[int, int]: (dwLowDateTime, dwHighDateTime) as unsigned 32-bit integers.
    Raises:
        TypeError: If timestamp_us is not an integer.
        ValueError: If timestamp_us is outside the representable 64-bit unsigned FILETIME range.
    """
    if not isinstance(timestamp_us, int) or isinstance(timestamp_us, bool):
        raise TypeError(f"timestamp_us must be an integer, got {type(timestamp_us).__name__}")

    if timestamp_us < MIN_TIMESTAMP_US or timestamp_us > MAX_TIMESTAMP_US:
        raise ValueError(
            f"timestamp_us {timestamp_us} is outside representable FILETIME range [{MIN_TIMESTAMP_US}, {MAX_TIMESTAMP_US}]"
        )

    # 1 microsecond = 10 intervals of 100 nanoseconds
    ticks = (timestamp_us * 10) + EPOCH_DIFF_FILETIME_TICKS
    dw_low = ticks & 0xFFFFFFFF
    dw_high = (ticks >> 32) & 0xFFFFFFFF
    return (dw_low, dw_high)


def filetime_to_utc_us(dw_low: int, dw_high: int) -> int:
    """Convert Windows FILETIME (dwLowDateTime, dwHighDateTime) to microsecond UTC timestamp.

    Raises:
        TypeError: If dw_low or dw_high is not an integer.
        ValueError: If dw_low or dw_high is outside unsigned 32-bit integer range.
    """
    if not isinstance(dw_low, int) or isinstance(dw_low, bool) or not isinstance(dw_high, int) or isinstance(dw_high, bool):
        raise TypeError("dw_low and dw_high must be integers")

    if not (0 <= dw_low <= 0xFFFFFFFF and 0 <= dw_high <= 0xFFFFFFFF):
        raise ValueError("dw_low and dw_high must be unsigned 32-bit integers (0 to 0xFFFFFFFF)")

    ticks = (dw_high << 32) | dw_low
    return (ticks - EPOCH_DIFF_FILETIME_TICKS) // 10


# --- Immutable Client Data Structures ---

@dataclass(frozen=True)
class OpcItemValue:
    """Immutable snapshot of an OPC DA item value exposed to clients."""
    opc_item_path: str
    value: Any
    quality: int
    source_timestamp_us: int
    filetime_low: int
    filetime_high: int
    value_type: int
    vartype: int
    access_rights: int = OPC_READABLE


@dataclass(frozen=True)
class OpcServerStatus:
    """Snapshot of OPC DA Server Status (OPCSERVERSTATUS)."""
    state: int
    start_time_us: int
    current_time_us: int
    last_update_time_us: int
    vendor_info: str
    major_version: int
    minor_version: int
    build_number: int
    group_count: int
    bandwidth: int = -1


@dataclass(frozen=True)
class OpcItemWriteResult:
    """Per-item write operation result."""
    target_identifier: str | int
    hresult: int = OPC_E_BADRIGHTS


@dataclass(frozen=True)
class OpcWriteResult:
    """Batch write operation result container."""
    overall_hresult: int
    item_results: list[OpcItemWriteResult] = field(default_factory=list)


@dataclass
class OpcGroupConfig:
    """Configuration state for an active OPC Group."""
    group_handle: int
    name: str
    active: bool = True
    requested_update_rate_ms: int = 1000
    revised_update_rate_ms: int = 1000
    time_bias_minutes: int = 0
    percent_deadband: float = 0.0
    lcid: int = 0
    client_handle: int = 0
