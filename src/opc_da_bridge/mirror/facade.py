"""SDK-neutral OPC DA Read Facade wrapping BoundedLatestValueCache.

Provides a platform-neutral, read-only OPC DA server presentation layer:
- Predictable tag namespace with alias resolution and address space browsing.
- Fully decoupled from TLS transport.
- Atomic snapshots returning immutable OpcItemValue copies (no internal reference leaks).
- Dynamic staleness detection observable on read without requiring background loops.
- Strict non-Good quality preservation for missing, stale, disconnected, or disk-restored items.
- Strict rejection of all writes (DA2 Write and DA3 WriteVQT) returning OPC_E_BADRIGHTS (0xC0040006).
- Group lifecycle, update rate negotiation/clamping, and Advise change subscriptions.
- Orderly shutdown notification mechanism.
"""
from __future__ import annotations

import collections
import logging
import threading
import time
from typing import Any, Callable

from opc_da_bridge.core.cache import BoundedLatestValueCache
from opc_da_bridge.core.model import (
    CachedItemValue,
    OPC_QUALITY_BAD_COMM_FAILURE,
    OPC_QUALITY_BAD_LAST_KNOWN_VALUE,
    OPC_QUALITY_BAD_NOT_CONNECTED,
    OPC_QUALITY_GOOD,
    is_quality_good,
    utc_now_us,
)
from opc_da_bridge.mirror.contracts import (
    OPC_DS_CACHE,
    OPC_DS_DEVICE,
    OPC_READABLE,
    OPC_STATUS_RUNNING,
    OpcGroupConfig,
    OpcItemValue,
    OpcItemWriteResult,
    OpcServerStatus,
    OpcWriteResult,
    utc_us_to_filetime,
    valuetype_to_vartype,
)
from opc_da_bridge.mirror.hresult import (
    OPC_E_BADRIGHTS,
    OPC_E_DUPLICATENAME,
    OPC_E_INVALIDHANDLE,
    OPC_E_UNKNOWNITEMNAME,
    S_FALSE,
    S_OK,
)

logger = logging.getLogger(__name__)


class OpcGroup:
    """Internal representation of an OPC Group within the Read Facade."""

    def __init__(self, config: OpcGroupConfig) -> None:
        self.config = config
        self._items: dict[int, tuple[str, int]] = {}  # server_handle -> (opc_path, client_handle)
        self._path_to_server_handle: dict[str, int] = {}
        self._next_item_handle: int = 1
        self._last_values: dict[str, Any] = {}
        self._last_qualities: dict[str, int] = {}
        self._last_fired_monotonic: float | None = None
        self.advise_callback: Callable[[int, list[OpcItemValue]], None] | None = None

    def add_item(self, path: str, client_handle: int) -> int:
        server_handle = self._next_item_handle
        self._next_item_handle += 1
        self._items[server_handle] = (path, client_handle)
        self._path_to_server_handle[path] = server_handle
        return server_handle

    def remove_item(self, server_handle: int) -> bool:
        if server_handle in self._items:
            path, _ = self._items.pop(server_handle)
            self._path_to_server_handle.pop(path, None)
            self._last_values.pop(path, None)
            self._last_qualities.pop(path, None)
            return True
        return False


class OpcDaReadFacade:
    """Thread-safe, platform-neutral OPC DA Read Facade over BoundedLatestValueCache."""

    def __init__(
        self,
        cache: BoundedLatestValueCache,
        vendor_info: str = "OPC-DA-Bridge Telemetry Mirror (Read-Only)",
        min_update_rate_ms: int = 50,
        max_update_rate_ms: int = 60000,
    ) -> None:
        self.cache = cache
        self.vendor_info = vendor_info
        self.min_update_rate_ms = min_update_rate_ms
        self.max_update_rate_ms = max_update_rate_ms

        self._start_time_us = utc_now_us()
        self._lock = threading.RLock()

        # Predictable alias mappings
        self._aliases: dict[str, str] = {
            "OPCBridge.Mirror.Lab.Temperature": "Simulated.Furnace.Temperature",
            "OPCBridge.Mirror.Lab.Pressure": "Simulated.Line.Pressure",
        }

        # Active groups: group_handle -> OpcGroup
        self._groups: dict[int, OpcGroup] = {}
        self._group_name_to_handle: dict[str, int] = {}
        self._next_group_handle: int = 1

        # Registered client shutdown callbacks: list of callback(reason)
        self._shutdown_callbacks: list[Callable[[str], None]] = []

    def register_alias(self, alias_path: str, target_cache_path: str) -> None:
        """Register a predictable alias path mapping to an internal cache item path."""
        with self._lock:
            self._aliases[alias_path] = target_cache_path

    def resolve_path(self, path: str) -> str:
        """Resolve tag path checking aliases first, then raw path."""
        with self._lock:
            return self._aliases.get(path, path)

    def is_valid_item_path(self, path: str) -> bool:
        """Check if tag path is known in aliases or currently in cache."""
        with self._lock:
            if path in self._aliases:
                return True
            resolved = self._aliases.get(path, path)
            with self.cache._lock:
                return resolved in self.cache._items

    def browse_items(self, prefix: str = "") -> list[str]:
        """Return predictable, sorted list of all browsable item paths matching prefix."""
        with self._lock:
            all_paths = set(self._aliases.keys())
            with self.cache._lock:
                all_paths.update(self.cache._items.keys())
            matched = [p for p in all_paths if p.startswith(prefix)]
            return sorted(matched)

    # --- Synchronous Reading (IOPCSyncIO / IOPCItemIO) ---

    def read_sync(
        self,
        item_paths: list[str],
        source: int = OPC_DS_CACHE,
        now: float | None = None,
    ) -> list[tuple[OpcItemValue | None, int]]:
        """Synchronously read items returning immutable OpcItemValue snapshots and per-item HRESULTs.

        Uses cache.snapshot_batch() to guarantee atomic staleness evaluation and deep-copy
        immutability under a single cache lock acquisition.
        """
        with self._lock:
            resolved_paths = [self.resolve_path(p) for p in item_paths]

        # Single lock acquisition on cache evaluates staleness and returns defensive copies
        snapshot_dict = self.cache.snapshot_batch(resolved_paths, now=now)

        results: list[tuple[OpcItemValue | None, int]] = []
        for requested_path, resolved_path in zip(item_paths, resolved_paths):
            cached = snapshot_dict.get(resolved_path)

            if cached is None:
                # Item unknown in cache
                results.append((None, OPC_E_UNKNOWNITEMNAME))
                continue

            # Ensure value payload is immutable
            safe_val = cached.value
            if isinstance(safe_val, (bytearray, memoryview)):
                safe_val = bytes(safe_val)
            elif isinstance(safe_val, list):
                safe_val = tuple(safe_val)

            # Convert cached item into an immutable OpcItemValue snapshot
            low, high = utc_us_to_filetime(cached.source_timestamp_us)
            item_val = OpcItemValue(
                opc_item_path=requested_path,
                value=safe_val,
                quality=cached.quality,
                source_timestamp_us=cached.source_timestamp_us,
                filetime_low=low,
                filetime_high=high,
                value_type=cached.value_type,
                vartype=valuetype_to_vartype(cached.value_type),
                access_rights=OPC_READABLE,
            )
            results.append((item_val, S_OK))

        return results

    def read_item_io(
        self,
        item_paths: list[str],
        max_ages_ms: list[int] | None = None,
    ) -> list[tuple[OpcItemValue | None, int]]:
        """OPC DA 3.0 IOPCItemIO::Read implementation (direct read without groups)."""
        return self.read_sync(item_paths, source=OPC_DS_CACHE)

    # --- Strict Write Rejection (IOPCSyncIO::Write, IOPCSyncIO2::WriteVQT, IOPCItemIO::WriteVQT) ---

    def write_sync(
        self,
        item_paths: list[str],
        values: list[Any],
    ) -> OpcWriteResult:
        """Strictly reject synchronous write requests with OPC_E_BADRIGHTS (0xC0040006).

        Guarantees zero mutation to the underlying cache or industrial source.
        """
        item_results: list[OpcItemWriteResult] = []
        for path in item_paths:
            item_results.append(OpcItemWriteResult(target_identifier=path, hresult=OPC_E_BADRIGHTS))

        # Overall method returns S_FALSE when all or some items fail per-item
        return OpcWriteResult(overall_hresult=S_FALSE, item_results=item_results)

    def write_vqt(
        self,
        item_paths: list[str],
        values: list[Any],
        qualities: list[int] | None = None,
        timestamps_us: list[int] | None = None,
    ) -> OpcWriteResult:
        """Strictly reject IOPCSyncIO2::WriteVQT / IOPCItemIO::WriteVQT write requests."""
        item_results: list[OpcItemWriteResult] = []
        for path in item_paths:
            item_results.append(OpcItemWriteResult(target_identifier=path, hresult=OPC_E_BADRIGHTS))

        return OpcWriteResult(overall_hresult=S_FALSE, item_results=item_results)

    # --- Group Management (IOPCServer::AddGroup, IOPCGroupStateMgt, IOPCItemMgt) ---

    def create_group(
        self,
        name: str,
        requested_update_rate_ms: int = 1000,
        client_handle: int = 0,
        active: bool = True,
        percent_deadband: float = 0.0,
    ) -> tuple[int, int]:
        """Create a new group and negotiate update rate.

        Returns:
            tuple[int, int]: (group_handle, revised_update_rate_ms) or raises on duplicate.
        """
        with self._lock:
            if name in self._group_name_to_handle:
                raise ValueError(f"Group name '{name}' already exists (OPC_E_DUPLICATENAME)")

            # Clamping / revising requested update rate
            revised_rate = max(self.min_update_rate_ms, min(requested_update_rate_ms, self.max_update_rate_ms))
            if requested_update_rate_ms <= 0:
                revised_rate = self.min_update_rate_ms

            group_handle = self._next_group_handle
            self._next_group_handle += 1

            config = OpcGroupConfig(
                group_handle=group_handle,
                name=name,
                active=active,
                requested_update_rate_ms=requested_update_rate_ms,
                revised_update_rate_ms=revised_rate,
                percent_deadband=percent_deadband,
                client_handle=client_handle,
            )
            group = OpcGroup(config)
            self._groups[group_handle] = group
            self._group_name_to_handle[name] = group_handle

            return (group_handle, revised_rate)

    def remove_group(self, group_handle: int) -> bool:
        """Remove an existing group."""
        with self._lock:
            group = self._groups.pop(group_handle, None)
            if group:
                self._group_name_to_handle.pop(group.config.name, None)
                return True
            return False

    def add_items_to_group(
        self,
        group_handle: int,
        item_paths: list[str],
        client_handles: list[int],
    ) -> list[tuple[int, int]]:
        """Add items to group. Validates item paths and array lengths.

        Returns list of (server_handle, hresult). Invalid/unknown paths return (0, OPC_E_UNKNOWNITEMNAME).
        """
        if len(item_paths) != len(client_handles):
            raise ValueError(
                f"Length mismatch: {len(item_paths)} item_paths vs {len(client_handles)} client_handles (E_INVALIDARG)"
            )

        with self._lock:
            group = self._groups.get(group_handle)
            if not group:
                raise KeyError(f"Invalid group handle {group_handle} (OPC_E_INVALIDHANDLE)")

            results: list[tuple[int, int]] = []
            for path, c_handle in zip(item_paths, client_handles):
                if not self.is_valid_item_path(path):
                    results.append((0, OPC_E_UNKNOWNITEMNAME))
                else:
                    s_handle = group.add_item(path, c_handle)
                    results.append((s_handle, S_OK))
            return results

    def remove_items_from_group(
        self,
        group_handle: int,
        server_handles: list[int],
    ) -> list[int]:
        """Remove items from group. Returns per-item HRESULTs."""
        with self._lock:
            group = self._groups.get(group_handle)
            if not group:
                raise KeyError(f"Invalid group handle {group_handle}")

            results: list[int] = []
            for h in server_handles:
                success = group.remove_item(h)
                results.append(S_OK if success else OPC_E_INVALIDHANDLE)
            return results

    def read_group_sync(
        self,
        group_handle: int,
        server_handles: list[int] | None = None,
        now: float | None = None,
    ) -> list[tuple[int, OpcItemValue | None, int]]:
        """Synchronously read items belonging to a group, preserving exact 1:1 cardinality.

        Returns list of (client_handle, OpcItemValue, HRESULT).
        Invalid server handles yield (0, None, OPC_E_INVALIDHANDLE) in place.
        """
        with self._lock:
            group = self._groups.get(group_handle)
            if not group:
                raise KeyError(f"Invalid group handle {group_handle}")

            handles_to_read = server_handles if server_handles is not None else list(group._items.keys())
            item_specs = [group._items.get(h) for h in handles_to_read]

        # Gather paths for valid items
        valid_indices = [i for i, spec in enumerate(item_specs) if spec is not None]
        valid_paths = [item_specs[i][0] for i in valid_indices]

        # Batch read valid paths
        read_results = self.read_sync(valid_paths, now=now) if valid_paths else []
        read_map = dict(zip(valid_indices, read_results))

        output: list[tuple[int, OpcItemValue | None, int]] = []
        for i, spec in enumerate(item_specs):
            if spec is None:
                # Invalid server handle: preserve exact cardinality with error code
                output.append((0, None, OPC_E_INVALIDHANDLE))
            else:
                path, c_handle = spec
                val, hr = read_map[i]
                output.append((c_handle, val, hr))

        return output

    # --- Asynchronous Subscriptions & Advise Callbacks (IConnectionPoint, IOPCDataCallback) ---

    def advise_group(
        self,
        group_handle: int,
        callback: Callable[[int, list[OpcItemValue]], None],
    ) -> None:
        """Establish an asynchronous Advise connection on a group (IConnectionPoint::Advise)."""
        with self._lock:
            group = self._groups.get(group_handle)
            if not group:
                raise KeyError(f"Invalid group handle {group_handle}")
            group.advise_callback = callback

    def unadvise_group(self, group_handle: int) -> None:
        """Tear down an asynchronous Advise connection on a group."""
        with self._lock:
            group = self._groups.get(group_handle)
            if group:
                group.advise_callback = None

    def poll_group_subscription(
        self,
        group_handle: int,
        now: float | None = None,
    ) -> int:
        """Evaluate item updates for group subscription and trigger Advise callback if values or qualities changed.

        Respects revised_update_rate_ms using monotonic time.
        Callback is invoked outside lock to guarantee re-entrant safety and isolate exceptions.
        Returns count of updated items delivered.
        """
        current_now = now if now is not None else time.monotonic()

        with self._lock:
            group = self._groups.get(group_handle)
            if not group or not group.config.active or not group.advise_callback:
                return 0

            # Enforce revised update rate interval
            interval_s = group.config.revised_update_rate_ms / 1000.0
            if group._last_fired_monotonic is not None:
                if (current_now - group._last_fired_monotonic) < interval_s:
                    return 0

            paths = [item[0] for item in group._items.values()]

        # Read items outside group lock
        read_results = self.read_sync(paths, now=now)

        callback_to_invoke = None
        client_handle = 0
        changed_items: list[OpcItemValue] = []

        with self._lock:
            group = self._groups.get(group_handle)
            if not group or not group.config.active or not group.advise_callback:
                return 0

            for item_val, hr in read_results:
                if item_val and hr == S_OK:
                    path = item_val.opc_item_path
                    prev_val = group._last_values.get(path)
                    prev_qual = group._last_qualities.get(path)
                    is_initial = (path not in group._last_values)

                    # Trigger on initial reading, value change, OR quality change (even with same timestamp)
                    if is_initial or prev_val != item_val.value or prev_qual != item_val.quality:
                        group._last_values[path] = item_val.value
                        group._last_qualities[path] = item_val.quality
                        changed_items.append(item_val)

            if changed_items:
                group._last_fired_monotonic = current_now
                callback_to_invoke = group.advise_callback
                client_handle = group.config.client_handle

        # Execute client callback OUTSIDE self._lock to ensure re-entrancy and exception isolation
        if callback_to_invoke and changed_items:
            try:
                callback_to_invoke(client_handle, changed_items)
            except Exception as exc:
                logger.warning(f"Exception raised in client advise callback: {exc}")
            return len(changed_items)

        return 0

    # --- Server Status & Orderly Shutdown (IOPCServer::GetStatus, IOPCShutdown) ---

    def get_server_status(self) -> OpcServerStatus:
        """Return OPCSERVERSTATUS snapshot."""
        now_us = utc_now_us()
        with self._lock:
            group_count = len(self._groups)
            all_items = self.cache.get_all()
            last_upd_us = max([it.source_timestamp_us for it in all_items], default=self._start_time_us)

        return OpcServerStatus(
            state=OPC_STATUS_RUNNING,
            start_time_us=self._start_time_us,
            current_time_us=now_us,
            last_update_time_us=last_upd_us,
            vendor_info=self.vendor_info,
            major_version=1,
            minor_version=0,
            build_number=2,
            group_count=group_count,
            bandwidth=-1,
        )

    def register_shutdown_callback(self, callback: Callable[[str], None]) -> None:
        """Register a client callback to receive orderly shutdown requests (IOPCShutdown)."""
        with self._lock:
            self._shutdown_callbacks.append(callback)

    def request_shutdown(self, reason: str = "Server service stopping") -> int:
        """Trigger orderly shutdown request to all connected clients."""
        with self._lock:
            callbacks = list(self._shutdown_callbacks)

        count = 0
        for cb in callbacks:
            try:
                cb(reason)
                count += 1
            except Exception as exc:
                logger.warning(f"Error invoking shutdown callback: {exc}")
        return count
