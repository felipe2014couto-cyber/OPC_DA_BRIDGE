"""Small, read-only laboratory DA-to-UA gateway.

The adapters are deliberately isolated from the existing transport pipeline.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import sys
import time
from typing import Any, Protocol

from opc_da_bridge.core.cache import BoundedLatestValueCache
from opc_da_bridge.core.model import ItemSample, ValueType

UA_BAD_NO_COMMUNICATION = 0x80310000
COM_DISCONNECTED_HRESULTS = {
    0x80010007,  # RPC_E_SERVER_DIED
    0x80010012,  # RPC_E_SERVER_DIED_DNE
    0x80010108,  # RPC_E_DISCONNECTED
    0x80010114,  # RPC_E_INVALID_OBJECT
    0x800706BA,  # RPC_S_SERVER_UNAVAILABLE
    0x80080008,  # CO_E_SERVER_STOPPING
}


@dataclass(frozen=True)
class TagConfig:
    item_id: str
    node_id: str


@dataclass(frozen=True)
class DaReading:
    item_id: str
    value: Any
    quality: int
    source_timestamp_us: int | None
    python_type: str
    error: str | None = None


class ReadOnlyDaSource(Protocol):
    def connect(self, prog_id: str) -> None: ...
    def read(self, item_ids: list[str]) -> list[DaReading]: ...
    def close(self) -> None: ...


class Gateway:
    def __init__(self, source: ReadOnlyDaSource, cache: BoundedLatestValueCache,
                 tags: list[TagConfig], prog_id: str, logger: logging.Logger | None = None) -> None:
        self.source, self.cache, self.tags = source, cache, tags
        self.logger = logger or logging.getLogger("opc_da_bridge.lab")
        self.connected = False
        self.session = "lab-session"
        self.sequence = 0
        self._prog_id = prog_id

    def poll(self) -> None:
        try:
            if not self.connected:
                if self.sequence:
                    self.session = f"lab-{time.time_ns()}"
                self.source.connect(self._prog_id)
                self.connected = True
                self.cache.bind_active_session("opc-da-lab", self.session)
                self.logger.info("connected to OPC DA ProgID %s", self._prog_id)
            readings = self.source.read([tag.item_id for tag in self.tags])
            by_id = {r.item_id: r for r in readings}
            self.sequence += 1
            for idx, tag in enumerate(self.tags, 1):
                reading = by_id.get(tag.item_id)
                error = reading.error if reading else "no result returned for configured item"
                if error is None:
                    try:
                        # Types are inferred from returned values, never from tag names.
                        value_type = infer_value_type(reading.value)
                    except TypeError as exc:
                        error = f"unsupported returned type: {exc}"
                if error:
                    self.logger.error("OPC DA item read failed for %s: %s", tag.item_id, error)
                    previous = self.cache.snapshot_batch([tag.item_id])[tag.item_id]
                    if previous is None:
                        continue
                    value = previous.value
                    value_type = ValueType(previous.value_type)
                    source_ts = previous.source_timestamp_us or None
                    quality = 0x00
                else:
                    value = reading.value
                    quality = reading.quality
                    source_ts = reading.source_timestamp_us
                ts = source_ts if valid_timestamp(source_ts) else 0
                if ts == 0:
                    self.logger.warning("source timestamp missing/invalid for %s; publishing null UA SourceTimestamp", tag.item_id)
                self.cache.put_sample(ItemSample.create(
                    item_id=idx, opc_item_path=tag.item_id, value=value,
                    value_type=value_type, quality=quality, timestamp_us=ts,
                    source_id="opc-da-lab", session_id=self.session, sequence=self.sequence,
                ))
                self.logger.info("read %s returned type=%s", tag.item_id, reading.python_type)
        except Exception as exc:
            if self.connected:
                self.logger.error("OPC DA disconnected: %s", exc)
                self.cache.mark_disconnected(self.session)
                self.connected = False
            else:
                self.logger.error("OPC DA connection/read failed: %s", exc)
            try:
                self.source.close()
            except Exception:
                pass

    def set_prog_id(self, prog_id: str) -> None:
        self._prog_id = prog_id

def valid_timestamp(value: int | None) -> bool:
    if not isinstance(value, int) or isinstance(value, bool):
        return False
    try:
        datetime.fromtimestamp(value / 1_000_000, tz=timezone.utc)
        return value > 0
    except (OverflowError, OSError, ValueError):
        return False


def infer_value_type(value: Any) -> ValueType:
    if isinstance(value, bool):
        return ValueType.BOOL
    if isinstance(value, int):
        return ValueType.I16 if -32768 <= value <= 32767 else ValueType.I32
    if isinstance(value, float):
        return ValueType.F64
    if isinstance(value, str):
        return ValueType.STRING
    if isinstance(value, (bytes, bytearray, memoryview)):
        return ValueType.BLOB
    raise TypeError(f"unsupported OPC DA value type: {type(value).__name__}")


def da_quality_to_ua(quality: int, connected: bool = True, stale: bool = False) -> int:
    """Map DA major quality to OPC UA StatusCode numeric values."""
    if not connected:
        return UA_BAD_NO_COMMUNICATION
    if stale:
        return UA_BAD_NO_COMMUNICATION
    major = quality & 0xC0
    if major == 0xC0:
        return 0x00000000  # Good
    if major == 0x40:
        return 0x40000000  # Uncertain
    return 0x80000000  # Bad


def is_com_disconnected_error(error: Exception) -> bool:
    hresult = getattr(error, "hresult", None)
    return isinstance(hresult, int) and (hresult & 0xFFFFFFFF) in COM_DISCONNECTED_HRESULTS


class Pywin32DaSource:
    """Late-bound OPC Automation client; performs item reads only, never writes."""
    def connect(self, prog_id: str) -> None:
        if sys.platform != "win32":
            raise RuntimeError("OPC DA COM source is available only on Windows")
        import pythoncom  # type: ignore[import-not-found]
        import win32com.client  # type: ignore[import-not-found]
        pythoncom.CoInitialize()
        self._pythoncom = pythoncom
        self._com_initialized = True
        self.server = win32com.client.Dispatch("OPC.Automation.1")
        self.server.Connect(prog_id)
        self.group = self.server.OPCGroups.Add("OPC-DA-Bridge-Lab")
        self.group.IsActive = True
        self.items = self.group.OPCItems
        self.item_objects: dict[str, Any] = {}

    def read(self, item_ids: list[str]) -> list[DaReading]:
        result = []
        for index, item_id in enumerate(item_ids, 1):
            try:
                item = self.item_objects.get(item_id)
                if item is None:
                    item = self.items.AddItem(item_id, index)
                    self.item_objects[item_id] = item
                # Read(2) requests the device value. No OPC Automation write API is called.
                value, raw_quality, raw_ts = item.Read(2)
                ts_us = None
                if raw_ts:
                    try:
                        ts_us = int(raw_ts.astimezone(timezone.utc).timestamp() * 1_000_000)
                    except (AttributeError, TypeError, ValueError, OverflowError):
                        pass
                result.append(DaReading(item_id, value, int(raw_quality) & 0xFFFF, ts_us,
                                        type(value).__name__))
            except Exception as exc:
                if is_com_disconnected_error(exc):
                    raise
                result.append(DaReading(item_id, None, 0, None, "NoneType", str(exc)))
        return result

    def close(self) -> None:
        if getattr(self, "server", None) is not None:
            try:
                self.server.Disconnect()
            finally:
                self.server = None
        if getattr(self, "_com_initialized", False):
            self._pythoncom.CoUninitialize()
            self._com_initialized = False
