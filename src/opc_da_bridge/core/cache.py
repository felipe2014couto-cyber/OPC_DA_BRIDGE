"""Bounded latest-value cache with monotonic sequence validation and stale/disconnect handling.

This cache maintains the current operational state of mirrored OPC DA tags.
It implements:
- Bounded capacity with deterministic LRU eviction.
- Source identity and active session fencing (rejecting packets from retired/unbound sessions).
- Monotonic session sequence tracking (duplicate idempotence & out-of-order rejection).
- Stale detection with timeout expiration.
- Disconnect handling preserving last known value and original source timestamp.
- Disk snapshot restore enforcing non-Good quality on cold/warm restart with strict capacity bounds.
"""
from __future__ import annotations

import collections
import copy
from dataclasses import asdict
from enum import Enum
import json
from pathlib import Path
import threading
import time
from typing import Any

from opc_da_bridge.core.model import (
    CachedItemValue,
    ItemSample,
    ItemStatus,
    OPC_QUALITY_BAD_COMM_FAILURE,
    OPC_QUALITY_BAD_LAST_KNOWN_VALUE,
    OPC_QUALITY_BAD_NOT_CONNECTED,
    OPC_QUALITY_GOOD,
    OPC_QUALITY_MASK,
    decode_value,
    is_quality_good,
)


class UpdateResult(Enum):
    ACCEPTED = "ACCEPTED"
    DUPLICATE_IDEMPOTENT = "DUPLICATE_IDEMPOTENT"
    OUT_OF_ORDER_REJECTED = "OUT_OF_ORDER_REJECTED"
    STALE_SESSION_REJECTED = "STALE_SESSION_REJECTED"
    UNBOUND_SESSION_REJECTED = "UNBOUND_SESSION_REJECTED"
    EVICTED_OLD_ENTRY = "EVICTED_OLD_ENTRY"


class BoundedLatestValueCache:
    """Thread-safe, bounded latest-value cache for mirrored OPC DA items."""

    def __init__(
        self,
        max_items: int = 10000,
        stale_timeout_s: float = 5.0,
        max_tracked_sessions: int = 128,
        max_sources: int = 1000,
    ) -> None:
        if max_items < 1:
            raise ValueError("max_items must be at least 1")
        if stale_timeout_s <= 0:
            raise ValueError("stale_timeout_s must be positive")
        if max_tracked_sessions < 1:
            raise ValueError("max_tracked_sessions must be at least 1")
        if max_sources < 1:
            raise ValueError("max_sources must be at least 1")

        self.max_items = max_items
        self.stale_timeout_s = stale_timeout_s
        self.max_tracked_sessions = max_tracked_sessions
        self.max_sources = max_sources

        self._lock = threading.RLock()
        # OrderedDict used for LRU ordering by path
        self._items: collections.OrderedDict[str, CachedItemValue] = collections.OrderedDict()
        self._path_by_id: dict[int, str] = {}

        # Source identity and session fencing
        self._active_sessions: dict[str, str] = {}  # source_id -> active session_id
        self._retired_sessions: collections.OrderedDict[str, bool] = collections.OrderedDict()

        # Bounded session sequence tracking: session_id -> highest sequence seen
        self._session_sequences: collections.OrderedDict[str, int] = collections.OrderedDict()
        # Per-item tracking: path -> (last_session_id, last_sequence)
        self._item_sequences: dict[str, tuple[str, int]] = {}

        # Metrics
        self.stats = {
            "updates_accepted": 0,
            "duplicates_ignored": 0,
            "out_of_order_rejected": 0,
            "stale_session_rejected": 0,
            "unbound_session_rejected": 0,
            "evictions": 0,
            "stale_transitions": 0,
            "disconnect_transitions": 0,
        }

    @property
    def size(self) -> int:
        """Current number of items in cache."""
        with self._lock:
            return len(self._items)

    def bind_active_session(self, source_id: str, session_id: str) -> None:
        """Explicitly bind an active session to a source identity, retiring any prior session."""
        with self._lock:
            if source_id not in self._active_sessions and len(self._active_sessions) >= self.max_sources:
                raise ValueError(
                    f"Exceeded max_sources limit ({self.max_sources}). Cannot bind source '{source_id}'."
                )
            old_session = self._active_sessions.get(source_id)
            if old_session and old_session != session_id:
                self._retired_sessions[old_session] = True
                while len(self._retired_sessions) > self.max_tracked_sessions:
                    self._retired_sessions.popitem(last=False)
            self._active_sessions[source_id] = session_id

    def _prune_session_sequences(self) -> None:
        """Prune inactive sessions from session_sequences when exceeding max_tracked_sessions,
        preserving sequence tracking for all currently active sessions."""
        active_session_ids = set(self._active_sessions.values())
        while len(self._session_sequences) > self.max_tracked_sessions:
            candidate = None
            for sess_id in self._session_sequences:
                if sess_id not in active_session_ids:
                    candidate = sess_id
                    break
            if candidate is not None:
                del self._session_sequences[candidate]
            else:
                break

    def put_sample(self, sample: ItemSample) -> UpdateResult:
        """Process an incoming sample and update the latest-value cache.

        Enforces:
        1. Session fencing: Accept samples ONLY for a session explicitly activated
           by bind_active_session. Reject samples from retired or unbound/non-active sessions.
        2. Session-global monotonic ordering:
           - sequence > max_session_sequence: Accepted, update state.
           - sequence == max_session_sequence: Other items in the same batch accepted;
             exact duplicate for this item ignored idempotently.
           - sequence < max_session_sequence: Out-of-order rejected (even for new tag paths).
        """
        with self._lock:
            source_id = sample.source_id or "default_source"
            session_id = sample.session_id
            seq = sample.sequence
            path = sample.opc_item_path

            # 1. Session fencing check
            if session_id in self._retired_sessions:
                self.stats["stale_session_rejected"] += 1
                return UpdateResult.STALE_SESSION_REJECTED

            if source_id not in self._active_sessions:
                self.stats["unbound_session_rejected"] += 1
                return UpdateResult.UNBOUND_SESSION_REJECTED

            if self._active_sessions[source_id] != session_id:
                self.stats["stale_session_rejected"] += 1
                return UpdateResult.STALE_SESSION_REJECTED

            # 2. Session-global monotonic order check
            session_max_seq = self._session_sequences.get(session_id)
            if session_max_seq is not None:
                if seq < session_max_seq:
                    self.stats["out_of_order_rejected"] += 1
                    return UpdateResult.OUT_OF_ORDER_REJECTED
                elif seq == session_max_seq:
                    last_item_sess, last_item_seq = self._item_sequences.get(path, (None, -1))
                    if last_item_sess == session_id and last_item_seq == seq:
                        self.stats["duplicates_ignored"] += 1
                        return UpdateResult.DUPLICATE_IDEMPOTENT

            # 3. Check global bounded capacity
            if path not in self._items and len(self._items) >= self.max_items:
                # Evict oldest item (FIFO/LRU)
                oldest_path, oldest_item = self._items.popitem(last=False)
                self._path_by_id.pop(oldest_item.item_id, None)
                self._item_sequences.pop(oldest_path, None)
                self.stats["evictions"] += 1

            # Decode typed value if not provided
            val = sample.value
            if val is None and sample.value_bytes:
                val, _ = decode_value(sample.value_type, sample.value_bytes)
            val = copy.deepcopy(val)
            if isinstance(val, (bytearray, memoryview)):
                val = bytes(val)

            cached_entry = CachedItemValue(
                item_id=sample.item_id,
                opc_item_path=path,
                value=val,
                raw_value_bytes=sample.value_bytes,
                value_type=sample.value_type,
                quality=sample.quality,
                source_timestamp_us=sample.timestamp_us,
                last_updated_at=time.monotonic(),
                source_id=source_id,
                session_id=session_id,
                sequence=seq,
                stale=False,
                disconnected=False,
                status=sample.status,
                error_code=sample.error_code,
            )

            # Store in cache and move to end (MRU)
            self._items[path] = cached_entry
            self._items.move_to_end(path)
            self._path_by_id[sample.item_id] = path

            # Update sequence trackers
            self._item_sequences[path] = (session_id, seq)
            if session_max_seq is None or seq > session_max_seq:
                self._session_sequences[session_id] = seq
                self._session_sequences.move_to_end(session_id)
                self._prune_session_sequences()

            self.stats["updates_accepted"] += 1
            return UpdateResult.ACCEPTED

    def get_by_path(self, path: str) -> CachedItemValue | None:
        """Lookup cached value by OPC path."""
        with self._lock:
            return self._items.get(path)

    def get_by_id(self, item_id: int) -> CachedItemValue | None:
        """Lookup cached value by numeric item ID."""
        with self._lock:
            path = self._path_by_id.get(item_id)
            if path is None:
                return None
            return self._items.get(path)

    def get_all(self) -> list[CachedItemValue]:
        """Return snapshot of all cached entries."""
        with self._lock:
            return list(self._items.values())

    def snapshot_batch(
        self,
        paths: list[str],
        now: float | None = None,
    ) -> dict[str, CachedItemValue | None]:
        """Atomically evaluate staleness and return isolated deep copies for requested paths.

        All operations are performed under a single acquisition of self._lock, preventing interleaved
        concurrent updates from corrupting batch consistency.
        Returned CachedItemValue objects are fresh copies whose value payloads are deeply copied
        (via copy.deepcopy, and bytearray/memoryview converted to bytes) ensuring consumer modifications
        to nested collections or buffers can never mutate the cache.
        """
        current_time = now if now is not None else time.monotonic()
        result: dict[str, CachedItemValue | None] = {}

        with self._lock:
            # 1. Dynamically evaluate staleness under the exact same lock section
            for item in self._items.values():
                if not item.stale and not item.disconnected:
                    if (current_time - item.last_updated_at) >= self.stale_timeout_s:
                        item.stale = True
                        item.quality = OPC_QUALITY_BAD_COMM_FAILURE
                        item.status = int(ItemStatus.STALE)
                        self.stats["stale_transitions"] += 1

            # 2. Extract and copy requested items
            for path in paths:
                item = self._items.get(path)
                if item is None:
                    result[path] = None
                else:
                    # Deep defensive copy of value payload to guarantee complete isolation
                    safe_val = copy.deepcopy(item.value)
                    if isinstance(safe_val, (bytearray, memoryview)):
                        safe_val = bytes(safe_val)

                    raw_bytes = bytes(item.raw_value_bytes) if item.raw_value_bytes is not None else b""

                    copied_entry = CachedItemValue(
                        item_id=item.item_id,
                        opc_item_path=item.opc_item_path,
                        value=safe_val,
                        raw_value_bytes=raw_bytes,
                        value_type=item.value_type,
                        quality=item.quality,
                        source_timestamp_us=item.source_timestamp_us,
                        last_updated_at=item.last_updated_at,
                        source_id=item.source_id,
                        session_id=item.session_id,
                        sequence=item.sequence,
                        stale=item.stale,
                        disconnected=item.disconnected,
                        status=item.status,
                        error_code=item.error_code,
                    )
                    result[path] = copied_entry

        return result

    def check_and_update_stale(self, now: float | None = None) -> list[CachedItemValue]:
        """Scan cache for items that exceeded stale_timeout_s and transition them."""
        current_time = now if now is not None else time.monotonic()
        stale_entries: list[CachedItemValue] = []

        with self._lock:
            for item in self._items.values():
                if not item.stale and not item.disconnected:
                    if (current_time - item.last_updated_at) >= self.stale_timeout_s:
                        item.stale = True
                        item.quality = OPC_QUALITY_BAD_COMM_FAILURE
                        item.status = int(ItemStatus.STALE)
                        stale_entries.append(item)
                        self.stats["stale_transitions"] += 1

        return stale_entries

    def mark_disconnected(self, session_id: str | None = None) -> int:
        """Mark items as disconnected when source transport is lost or closed."""
        count = 0
        with self._lock:
            if session_id:
                # Mark session retired
                self._retired_sessions[session_id] = True
                while len(self._retired_sessions) > self.max_tracked_sessions:
                    self._retired_sessions.popitem(last=False)
                # Unbind active session for sources using this session
                sources_to_unbind = [src for src, sid in self._active_sessions.items() if sid == session_id]
                for src in sources_to_unbind:
                    del self._active_sessions[src]

            for item in self._items.values():
                if session_id is None or item.session_id == session_id:
                    if not item.disconnected:
                        item.disconnected = True
                        item.stale = True
                        item.quality = OPC_QUALITY_BAD_NOT_CONNECTED
                        item.status = int(ItemStatus.DISCONNECTED)
                        count += 1
                        self.stats["disconnect_transitions"] += 1
        return count

    def clear(self) -> None:
        """Clear all entries and state."""
        with self._lock:
            self._items.clear()
            self._path_by_id.clear()
            self._active_sessions.clear()
            self._retired_sessions.clear()
            self._session_sequences.clear()
            self._item_sequences.clear()

    # --- Disk Snapshot & Restart Behavior ---

    def save_snapshot(self, filepath: str | Path) -> None:
        """Persist current cache state to disk for diagnostic/restart inspection."""
        with self._lock:
            data = {
                "version": 1,
                "saved_at_monotonic": time.monotonic(),
                "items": [item.to_dict() for item in self._items.values()],
            }
        p = Path(filepath)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def load_snapshot(self, filepath: str | Path) -> int:
        """Restore cache state from disk with STRICT capacity bounds and non-Good quality guarantee.

        RULE: Current state cache, not historical recovery. Restored items
        MUST NEVER be presented as Good quality to OPC clients!
        Restored items are assigned OPC_QUALITY_BAD_LAST_KNOWN_VALUE (0x14)
        with stale=True and disconnected=True, preserving last known value
        and source timestamp until fresh live telemetry is received.
        Also strictly enforces max_items limit on snapshot load.
        """
        p = Path(filepath)
        if not p.exists():
            return 0

        content = json.loads(p.read_text(encoding="utf-8"))
        items_data = content.get("items", [])
        now_mono = time.monotonic()
        restored_count = 0

        with self._lock:
            self.clear()
            # Enforce max_items limit on restore
            for d in items_data:
                if len(self._items) >= self.max_items:
                    break

                raw_bytes = bytes.fromhex(d.get("raw_value_hex", ""))
                val_type = int(d.get("value_type", 0))
                val = d.get("value")
                src_id = str(d.get("source_id", "restored_source"))

                entry = CachedItemValue(
                    item_id=int(d["item_id"]),
                    opc_item_path=str(d["opc_item_path"]),
                    source_id=src_id,
                    value=val,
                    raw_value_bytes=raw_bytes,
                    value_type=val_type,
                    # FORCED TO BAD_LAST_KNOWN_VALUE: Never Good from disk!
                    quality=OPC_QUALITY_BAD_LAST_KNOWN_VALUE,
                    source_timestamp_us=int(d.get("source_timestamp_us", 0)),
                    last_updated_at=now_mono,
                    session_id=str(d.get("session_id", "restored")),
                    sequence=int(d.get("sequence", 0)),
                    stale=True,
                    disconnected=True,
                    status=int(ItemStatus.STALE),
                    error_code=int(d.get("error_code", 0)),
                )
                self._items[entry.opc_item_path] = entry
                self._path_by_id[entry.item_id] = entry.opc_item_path
                self._item_sequences[entry.opc_item_path] = (entry.session_id, entry.sequence)
                restored_count += 1

        return restored_count
