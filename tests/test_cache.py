"""Unit tests for BoundedLatestValueCache.

Covers:
- Value, type, quality, source UTC timestamp, session, and sequence preservation.
- Explicit session binding requirement (rejection of unbound sessions).
- Bounded active sources enforcement (max_sources).
- Monotonic session sequencing: session-global ordering, duplicate idempotence, out-of-order rejection.
- Prevention of retired session samples restoring state.
- Bounded capacity and deterministic LRU eviction.
- Stale detection and timeout expiration preserving last known value and timestamp.
- Disconnect handling preserving last known value and timestamp.
- Disk restore guaranteeing non-Good quality on restart.
- Pruning inactive sessions without forgetting live active session sequences.
"""
from __future__ import annotations

import time
from pathlib import Path
import pytest

from opc_da_bridge.core.cache import BoundedLatestValueCache, UpdateResult
from opc_da_bridge.core.model import (
    ItemSample,
    ItemStatus,
    ValueType,
    OPC_QUALITY_BAD_COMM_FAILURE,
    OPC_QUALITY_BAD_LAST_KNOWN_VALUE,
    OPC_QUALITY_BAD_NOT_CONNECTED,
    OPC_QUALITY_GOOD,
    is_quality_good,
)


def test_value_type_quality_timestamp_session_sequence_preservation():
    cache = BoundedLatestValueCache(max_items=100)
    source_ts = 1710000000123456

    cache.bind_active_session("default_source", "sess_rig_alpha")

    sample = ItemSample.create(
        item_id=1,
        opc_item_path="Rig.Speed",
        value=3210.5,
        value_type=ValueType.F64,
        quality=OPC_QUALITY_GOOD,
        timestamp_us=source_ts,
        session_id="sess_rig_alpha",
        sequence=10,
    )

    res = cache.put_sample(sample)
    assert res == UpdateResult.ACCEPTED

    item = cache.get_by_path("Rig.Speed")
    assert item is not None
    assert item.item_id == 1
    assert item.opc_item_path == "Rig.Speed"
    assert item.value == 3210.5
    assert item.value_type == ValueType.F64
    assert item.quality == OPC_QUALITY_GOOD
    assert item.source_timestamp_us == source_ts
    assert item.session_id == "sess_rig_alpha"
    assert item.sequence == 10
    assert item.stale is False


def test_cache_rejects_unbound_session():
    """Cache must reject samples for sessions not explicitly activated by bind_active_session."""
    cache = BoundedLatestValueCache(max_items=10)

    sample = ItemSample.create(
        item_id=1,
        opc_item_path="Tag.Unbound",
        value=42,
        value_type=ValueType.I32,
        source_id="unbound_source",
        session_id="unbound_sess",
        sequence=1,
    )

    res = cache.put_sample(sample)
    assert res == UpdateResult.UNBOUND_SESSION_REJECTED
    assert cache.stats["unbound_session_rejected"] == 1
    assert cache.get_by_path("Tag.Unbound") is None


def test_bounded_active_sources_limit():
    """Cache must bound active-source state with configurable max_sources and reject exceeding attempts."""
    # Validation
    with pytest.raises(ValueError):
        BoundedLatestValueCache(max_sources=0)
    with pytest.raises(ValueError):
        BoundedLatestValueCache(max_sources=-2)

    cache = BoundedLatestValueCache(max_items=10, max_sources=2)
    cache.bind_active_session("src_1", "sess_1")
    cache.bind_active_session("src_2", "sess_2")

    # Replacing an existing source's session must succeed (source count does not increase)
    cache.bind_active_session("src_1", "sess_1_v2")

    # Attempting to bind a 3rd unique source must be rejected
    with pytest.raises(ValueError, match="max_sources"):
        cache.bind_active_session("src_3", "sess_3")


def test_replaced_session_samples_cannot_restore_state():
    """On replacing a source session, samples from the old session must be rejected and cannot restore state."""
    cache = BoundedLatestValueCache(max_items=10)

    # 1. Establish session 1 and write initial state
    cache.bind_active_session("src_1", "sess_1")
    s1 = ItemSample.create(
        item_id=1,
        opc_item_path="Valve.Status",
        value="OPEN",
        value_type=ValueType.STRING,
        source_id="src_1",
        session_id="sess_1",
        sequence=1,
    )
    assert cache.put_sample(s1) == UpdateResult.ACCEPTED
    assert cache.get_by_path("Valve.Status").value == "OPEN"

    # 2. Reconnect with session 2 and update state
    cache.bind_active_session("src_1", "sess_2")
    s2 = ItemSample.create(
        item_id=1,
        opc_item_path="Valve.Status",
        value="CLOSED",
        value_type=ValueType.STRING,
        source_id="src_1",
        session_id="sess_2",
        sequence=1,
    )
    assert cache.put_sample(s2) == UpdateResult.ACCEPTED
    assert cache.get_by_path("Valve.Status").value == "CLOSED"

    # 3. Straggler/delayed packet from session 1 arrives
    s_delayed = ItemSample.create(
        item_id=1,
        opc_item_path="Valve.Status",
        value="OPEN",
        value_type=ValueType.STRING,
        source_id="src_1",
        session_id="sess_1",
        sequence=5,
    )
    res = cache.put_sample(s_delayed)
    assert res == UpdateResult.STALE_SESSION_REJECTED
    assert cache.stats["stale_session_rejected"] == 1

    # State must not be restored or corrupted
    item = cache.get_by_path("Valve.Status")
    assert item.value == "CLOSED"
    assert item.session_id == "sess_2"


def test_session_global_monotonic_order():
    """Enforce session-global monotonic order:
    - Once batch sequence N is seen, older sequence < N is rejected even for different item paths.
    - Equal sequence for other items in the same batch remains accepted.
    - Exact per-item duplicate remains idempotent.
    """
    cache = BoundedLatestValueCache(max_items=10)
    cache.bind_active_session("src_rig", "sess_m")

    # Batch seq=5: Tag.A
    s_a5 = ItemSample.create(item_id=1, opc_item_path="Tag.A", value=10, value_type=ValueType.I32, source_id="src_rig", session_id="sess_m", sequence=5)
    assert cache.put_sample(s_a5) == UpdateResult.ACCEPTED

    # Batch seq=5: Tag.B (different item path in same batch) -> ACCEPTED
    s_b5 = ItemSample.create(item_id=2, opc_item_path="Tag.B", value=20, value_type=ValueType.I32, source_id="src_rig", session_id="sess_m", sequence=5)
    assert cache.put_sample(s_b5) == UpdateResult.ACCEPTED

    # Duplicate seq=5: Tag.A again -> DUPLICATE_IDEMPOTENT
    assert cache.put_sample(s_a5) == UpdateResult.DUPLICATE_IDEMPOTENT
    assert cache.stats["duplicates_ignored"] == 1

    # Out-of-order seq=4: Tag.C (different item path never seen before, but seq < 5) -> REJECTED
    s_c4 = ItemSample.create(item_id=3, opc_item_path="Tag.C", value=30, value_type=ValueType.I32, source_id="src_rig", session_id="sess_m", sequence=4)
    assert cache.put_sample(s_c4) == UpdateResult.OUT_OF_ORDER_REJECTED
    assert cache.get_by_path("Tag.C") is None
    assert cache.stats["out_of_order_rejected"] == 1

    # Advance session to seq=6: Tag.A
    s_a6 = ItemSample.create(item_id=1, opc_item_path="Tag.A", value=15, value_type=ValueType.I32, source_id="src_rig", session_id="sess_m", sequence=6)
    assert cache.put_sample(s_a6) == UpdateResult.ACCEPTED

    # Delayed seq=5: Tag.B (now < 6) -> REJECTED
    assert cache.put_sample(s_b5) == UpdateResult.OUT_OF_ORDER_REJECTED
    assert cache.stats["out_of_order_rejected"] == 2


def test_live_active_session_sequence_not_forgotten_during_prune():
    """Pruning session tracking maps must never evict the active session sequence tracker."""
    cache = BoundedLatestValueCache(max_items=10, max_tracked_sessions=2)

    # 1. Active session establishes sequence 20
    cache.bind_active_session("src_live", "sess_live")
    s_live = ItemSample.create(
        item_id=1, opc_item_path="Live.Tag", value=100, value_type=ValueType.I32,
        source_id="src_live", session_id="sess_live", sequence=20
    )
    assert cache.put_sample(s_live) == UpdateResult.ACCEPTED

    # 2. Cycle several temporary sessions on another source
    for i in range(1, 6):
        cache.bind_active_session("src_temp", f"sess_temp_{i}")
        s = ItemSample.create(
            item_id=2, opc_item_path="Temp.Tag", value=i, value_type=ValueType.I32,
            source_id="src_temp", session_id=f"sess_temp_{i}", sequence=i
        )
        cache.put_sample(s)

    # 3. Verify sess_live sequence tracking is still intact and not forgotten
    assert "sess_live" in cache._session_sequences
    assert cache._session_sequences["sess_live"] == 20

    # 4. Out-of-order packet for sess_live must still be rejected
    s_delayed = ItemSample.create(
        item_id=1, opc_item_path="Live.Tag", value=99, value_type=ValueType.I32,
        source_id="src_live", session_id="sess_live", sequence=18
    )
    assert cache.put_sample(s_delayed) == UpdateResult.OUT_OF_ORDER_REJECTED

    # 5. Newer sequence for sess_live must be accepted
    s_newer = ItemSample.create(
        item_id=1, opc_item_path="Live.Tag", value=101, value_type=ValueType.I32,
        source_id="src_live", session_id="sess_live", sequence=21
    )
    assert cache.put_sample(s_newer) == UpdateResult.ACCEPTED


def test_duplicate_idempotence():
    cache = BoundedLatestValueCache(max_items=100)
    cache.bind_active_session("default_source", "sess_1")

    sample_seq10 = ItemSample.create(
        item_id=1,
        opc_item_path="Rig.Pressure",
        value=150,
        value_type=ValueType.I32,
        quality=OPC_QUALITY_GOOD,
        timestamp_us=5000,
        session_id="sess_1",
        sequence=10,
    )
    res1 = cache.put_sample(sample_seq10)
    assert res1 == UpdateResult.ACCEPTED

    # Send exact duplicate
    res2 = cache.put_sample(sample_seq10)
    assert res2 == UpdateResult.DUPLICATE_IDEMPOTENT
    assert cache.stats["duplicates_ignored"] == 1


def test_out_of_order_rejection():
    cache = BoundedLatestValueCache(max_items=100)
    cache.bind_active_session("default_source", "sess_1")

    sample_seq10 = ItemSample.create(
        item_id=1,
        opc_item_path="Rig.Pressure",
        value=150,
        value_type=ValueType.I32,
        quality=OPC_QUALITY_GOOD,
        timestamp_us=5000,
        session_id="sess_1",
        sequence=10,
    )
    cache.put_sample(sample_seq10)

    # Older sequence arrives (seq=8 while at seq=10)
    sample_seq8 = ItemSample.create(
        item_id=1,
        opc_item_path="Rig.Pressure",
        value=145,
        value_type=ValueType.I32,
        quality=OPC_QUALITY_GOOD,
        timestamp_us=4000,
        session_id="sess_1",
        sequence=8,
    )
    res = cache.put_sample(sample_seq8)
    assert res == UpdateResult.OUT_OF_ORDER_REJECTED
    assert cache.stats["out_of_order_rejected"] == 1

    # Verify state did not regress
    item = cache.get_by_path("Rig.Pressure")
    assert item is not None
    assert item.value == 150
    assert item.source_timestamp_us == 5000
    assert item.sequence == 10


def test_session_transition_monotonicity():
    cache = BoundedLatestValueCache(max_items=100)

    # Session 1 ends at seq=50
    cache.bind_active_session("default_source", "sess_1")
    s1 = ItemSample.create(item_id=1, opc_item_path="Rig.Flow", value=10.0, value_type=ValueType.F64, session_id="sess_1", sequence=50)
    assert cache.put_sample(s1) == UpdateResult.ACCEPTED

    # Reconnection: explicitly bind new active session 2
    cache.bind_active_session("default_source", "sess_2")
    s2 = ItemSample.create(item_id=1, opc_item_path="Rig.Flow", value=12.0, value_type=ValueType.F64, session_id="sess_2", sequence=1)
    assert cache.put_sample(s2) == UpdateResult.ACCEPTED

    item = cache.get_by_path("Rig.Flow")
    assert item is not None
    assert item.session_id == "sess_2"
    assert item.sequence == 1
    assert item.value == 12.0


def test_bounded_capacity_and_eviction():
    cache = BoundedLatestValueCache(max_items=3)
    cache.bind_active_session("default_source", "sess_1")

    for i in range(1, 4):
        sample = ItemSample.create(
            item_id=i, opc_item_path=f"Tag.{i}", value=i, value_type=ValueType.I32,
            session_id="sess_1", sequence=1
        )
        cache.put_sample(sample)

    assert cache.size == 3
    assert cache.get_by_path("Tag.1") is not None

    # Adding 4th item must evict Tag.1 (oldest)
    sample4 = ItemSample.create(
        item_id=4, opc_item_path=f"Tag.4", value=4, value_type=ValueType.I32,
        session_id="sess_1", sequence=1
    )
    res = cache.put_sample(sample4)
    assert res == UpdateResult.ACCEPTED
    assert cache.size == 3
    assert cache.stats["evictions"] == 1

    assert cache.get_by_path("Tag.1") is None
    assert cache.get_by_id(1) is None
    assert cache.get_by_path("Tag.4") is not None


def test_stale_detection_and_timeout():
    cache = BoundedLatestValueCache(max_items=10, stale_timeout_s=0.2)
    cache.bind_active_session("default_source", "sess_1")
    source_ts = 1710000000000

    sample = ItemSample.create(
        item_id=1, opc_item_path="Tag.StaleTest", value=42, value_type=ValueType.I32,
        quality=OPC_QUALITY_GOOD, timestamp_us=source_ts, session_id="sess_1", sequence=1
    )
    cache.put_sample(sample)

    # Immediately check: not stale
    stale_items = cache.check_and_update_stale()
    assert len(stale_items) == 0

    # Wait for timeout expiration
    time.sleep(0.25)
    stale_items = cache.check_and_update_stale()
    assert len(stale_items) == 1

    item = cache.get_by_path("Tag.StaleTest")
    assert item is not None
    assert item.stale is True
    assert item.quality == OPC_QUALITY_BAD_COMM_FAILURE
    assert item.status == ItemStatus.STALE
    # Crucial: Value and source timestamp preserved!
    assert item.value == 42
    assert item.source_timestamp_us == source_ts


def test_disconnect_quality_and_last_timestamp():
    cache = BoundedLatestValueCache(max_items=10)
    cache.bind_active_session("default_source", "sess_vibe")
    source_ts = 1719999999000

    sample = ItemSample.create(
        item_id=1,
        opc_item_path="Rig.Vibration",
        value=0.045,
        value_type=ValueType.F64,
        quality=OPC_QUALITY_GOOD,
        timestamp_us=source_ts,
        session_id="sess_vibe",
        sequence=100,
    )
    cache.put_sample(sample)

    # Simulate transport disconnection
    count = cache.mark_disconnected("sess_vibe")
    assert count == 1

    item = cache.get_by_path("Rig.Vibration")
    assert item is not None
    assert item.disconnected is True
    assert item.stale is True
    assert item.quality == OPC_QUALITY_BAD_NOT_CONNECTED
    assert item.status == ItemStatus.DISCONNECTED
    # Crucial: Value and original source timestamp preserved!
    assert item.value == 0.045
    assert item.source_timestamp_us == source_ts


def test_disk_restore_enforces_not_good_quality(tmp_path: Path):
    cache = BoundedLatestValueCache(max_items=10)
    cache.bind_active_session("default_source", "sess_1")
    sample = ItemSample.create(
        item_id=1,
        opc_item_path="Tag.ToPersist",
        value="CRITICAL_ALARM",
        value_type=ValueType.STRING,
        quality=OPC_QUALITY_GOOD,
        timestamp_us=1234567,
        session_id="sess_1",
        sequence=1,
    )
    cache.put_sample(sample)

    snapshot_file = tmp_path / "snapshot.json"
    cache.save_snapshot(snapshot_file)
    assert snapshot_file.exists()

    # Create new fresh cache instance simulating service restart
    restored_cache = BoundedLatestValueCache(max_items=10)
    count = restored_cache.load_snapshot(snapshot_file)
    assert count == 1

    restored_item = restored_cache.get_by_path("Tag.ToPersist")
    assert restored_item is not None
    assert restored_item.value == "CRITICAL_ALARM"
    assert restored_item.source_timestamp_us == 1234567

    # STRICT RULE: Disk restore is current state cache, NOT historical recovery.
    # Restored items MUST NEVER have Good quality on restart!
    assert restored_item.quality == OPC_QUALITY_BAD_LAST_KNOWN_VALUE
    assert not is_quality_good(restored_item.quality)
    assert restored_item.stale is True
    assert restored_item.disconnected is True


def test_session_fencing_rejects_retired_and_non_active_sessions():
    cache = BoundedLatestValueCache(max_items=10)

    # 1. Establish session 1 for source_A and send live update
    cache.bind_active_session(source_id="source_A", session_id="sess_1")
    s1 = ItemSample.create(
        item_id=1,
        opc_item_path="Plant.ValveState",
        value="OPEN",
        value_type=ValueType.STRING,
        source_id="source_A",
        session_id="sess_1",
        sequence=1,
    )
    assert cache.put_sample(s1) == UpdateResult.ACCEPTED
    assert cache.get_by_path("Plant.ValveState").value == "OPEN"

    # 2. Source reconnects establishing session 2 for source_A
    cache.bind_active_session(source_id="source_A", session_id="sess_2")
    s2 = ItemSample.create(
        item_id=1,
        opc_item_path="Plant.ValveState",
        value="CLOSED",
        value_type=ValueType.STRING,
        source_id="source_A",
        session_id="sess_2",
        sequence=1,
    )
    assert cache.put_sample(s2) == UpdateResult.ACCEPTED
    assert cache.get_by_path("Plant.ValveState").value == "CLOSED"

    # 3. An old queued/delayed packet from session 1 arrives
    s_delayed = ItemSample.create(
        item_id=1,
        opc_item_path="Plant.ValveState",
        value="OPEN",  # Would corrupt state if accepted!
        value_type=ValueType.STRING,
        source_id="source_A",
        session_id="sess_1",
        sequence=2,
    )
    res = cache.put_sample(s_delayed)
    assert res == UpdateResult.STALE_SESSION_REJECTED
    assert cache.stats["stale_session_rejected"] == 1

    # State must remain CLOSED from session 2
    item = cache.get_by_path("Plant.ValveState")
    assert item.value == "CLOSED"
    assert item.session_id == "sess_2"


def test_snapshot_restore_enforces_bounded_max_items(tmp_path: Path):
    cache = BoundedLatestValueCache(max_items=20)
    cache.bind_active_session("default_source", "sess_1")
    for i in range(10):
        s = ItemSample.create(
            item_id=i, opc_item_path=f"Tag.{i}", value=i, value_type=ValueType.I32,
            session_id="sess_1", sequence=1
        )
        cache.put_sample(s)

    snapshot_file = tmp_path / "snap_bounded.json"
    cache.save_snapshot(snapshot_file)

    # Restoring into a cache with max_items=4
    small_cache = BoundedLatestValueCache(max_items=4)
    loaded = small_cache.load_snapshot(snapshot_file)
    assert loaded == 4
    assert small_cache.size == 4


def test_bounded_session_tracking_maps():
    cache = BoundedLatestValueCache(max_items=10, max_tracked_sessions=3)

    # Cycle 6 sessions for source_A
    for i in range(1, 7):
        cache.bind_active_session("source_A", f"sess_{i}")
        s = ItemSample.create(item_id=1, opc_item_path="Tag.Test", value=i, value_type=ValueType.I32, source_id="source_A", session_id=f"sess_{i}", sequence=i)
        cache.put_sample(s)

    # Check that retired and session sequence maps do not exceed max_tracked_sessions (3)
    assert len(cache._retired_sessions) <= 3
    assert len(cache._session_sequences) <= 3
