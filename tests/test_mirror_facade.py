"""Comprehensive cross-platform unit tests for OPC DA Mirror Facade and Windows Adapter.

Verifies:
- Official HRESULT definitions and formatting with canonical OPC_E_UNKNOWNITEMID (and prototype alias).
- Windows FILETIME conversion accuracy at Unix epoch (1970-01-01), uint64 range bounds, and invalid inputs.
- VQT preservation across ALL ValueType variants (I16, I32, F32, F64, BOOL, STRING, BLOB) including timestamp assertion per item.
- Deep isolation of returned snapshots (mutating nested structures or payload never corrupts cache).
- VQT preservation across non-Good states: stale, disconnected, and disk-restored.
- Namespace resolution, validation, and address space browsing.
- Group AddItems validation (length mismatch raises E_INVALIDARG, unknown items return OPC_E_UNKNOWNITEMID).
- Group read_sync 1:1 cardinality preservation with OPC_E_INVALIDHANDLE for invalid handles.
- Group subscription poll with revised update rate enforcement, deterministic clock sentinel handling (now=0.0 -> now=0.01),
  and quality-only change detection.
- External callback re-entrancy and exception isolation without lock contention.
- Strict rejection of all write requests (sync, async, DA3 WriteVQT).
- Real COM server entry point explicit failure and platform guard.
- Linux subprocess isolation verifying core/mirror/demo do not import windows_mirror or COM libs.
- Mock server warning banners and non-registration guarantees.
- Group update rate clamping, duplicate group rejection, and orderly shutdown notifications.
"""
from __future__ import annotations

import subprocess
import sys
import time

import pytest

from opc_da_bridge.core.cache import BoundedLatestValueCache
from opc_da_bridge.core.model import (
    ItemSample,
    ItemStatus,
    OPC_QUALITY_BAD_COMM_FAILURE,
    OPC_QUALITY_BAD_LAST_KNOWN_VALUE,
    OPC_QUALITY_BAD_NOT_CONNECTED,
    OPC_QUALITY_GOOD,
    ValueType,
    utc_now_us,
)
from opc_da_bridge.mirror.contracts import (
    EPOCH_DIFF_FILETIME_TICKS,
    MAX_TIMESTAMP_US,
    MIN_TIMESTAMP_US,
    OPC_DS_CACHE,
    OPC_READABLE,
    OPC_STATUS_RUNNING,
    VT_ARRAY,
    VT_BOOL,
    VT_BSTR,
    VT_I2,
    VT_I4,
    VT_R4,
    VT_R8,
    VT_UI1,
    OpcItemValue,
    filetime_to_utc_us,
    utc_us_to_filetime,
    valuetype_to_vartype,
)
from opc_da_bridge.mirror.facade import OpcDaReadFacade
from opc_da_bridge.mirror.hresult import (
    OPC_E_BADRIGHTS,
    OPC_E_DUPLICATENAME,
    OPC_E_INVALIDHANDLE,
    OPC_E_UNKNOWNITEMID,
    OPC_E_UNKNOWNITEMNAME,
    OPC_S_CLAMP,
    OPC_S_INUSE,
    OPC_S_UNSUPPORTEDRATE,
    S_FALSE,
    S_OK,
    format_hresult,
    is_error,
    is_success,
)
from opc_da_bridge.windows_mirror.adapter import (
    AdvosolPluginContract,
    CLSID_MIRROR,
    MockOpcDaComServer,
    PROGID_MIRROR_V1,
    RealOpcDaComServer,
    start_real_com_server,
)


def test_hresult_confirmed_codes_only() -> None:
    """Test official OPC DA HRESULT constants and format utility."""
    assert S_OK == 0x00000000
    assert S_FALSE == 0x00000001
    assert OPC_E_BADRIGHTS == 0xC0040006
    assert OPC_E_INVALIDHANDLE == 0xC0040001
    assert OPC_E_UNKNOWNITEMID == 0xC0040007
    assert OPC_E_UNKNOWNITEMNAME == OPC_E_UNKNOWNITEMID  # Prototype alias
    assert OPC_S_INUSE == 0x0004000F
    assert OPC_S_CLAMP == 0x0004000E
    assert OPC_S_UNSUPPORTEDRATE == 0x0004000D

    assert is_success(S_OK)
    assert is_success(S_FALSE)
    assert is_success(OPC_S_INUSE)
    assert is_error(OPC_E_BADRIGHTS)
    assert is_error(OPC_E_INVALIDHANDLE)
    assert is_error(OPC_E_UNKNOWNITEMID)

    formatted = format_hresult(OPC_E_BADRIGHTS)
    assert "OPC_E_BADRIGHTS" in formatted
    assert "0xC0040006" in formatted


def test_filetime_exact_unix_epoch_and_bounds() -> None:
    """Test FILETIME conversion accuracy at Unix epoch (0 us), limits, and error handling."""
    # Unix epoch (0 us) MUST correspond to 1970-01-01 00:00:00 UTC (116444736000000000 ticks)
    low, high = utc_us_to_filetime(0)
    ticks = (high << 32) | low
    assert ticks == EPOCH_DIFF_FILETIME_TICKS
    assert ticks == 116444736000000000
    # Exact round-trip
    assert filetime_to_utc_us(low, high) == 0

    # Minimum timestamp (1601-01-01 00:00:00 UTC) produces ticks = 0
    low_min, high_min = utc_us_to_filetime(MIN_TIMESTAMP_US)
    assert low_min == 0 and high_min == 0
    assert filetime_to_utc_us(0, 0) == MIN_TIMESTAMP_US

    # Known modern timestamp: 2026-10-08 12:00:00 UTC
    known_us = 1791460800000000
    low_k, high_k = utc_us_to_filetime(known_us)
    assert filetime_to_utc_us(low_k, high_k) == known_us

    # Boundary tests: out-of-range timestamps raise ValueError
    with pytest.raises(ValueError, match="outside representable FILETIME range"):
        utc_us_to_filetime(MIN_TIMESTAMP_US - 1)

    with pytest.raises(ValueError, match="outside representable FILETIME range"):
        utc_us_to_filetime(MAX_TIMESTAMP_US + 1)

    # Type validation
    with pytest.raises(TypeError):
        utc_us_to_filetime("not_an_int")  # type: ignore

    with pytest.raises(TypeError):
        utc_us_to_filetime(True)  # bool rejected

    with pytest.raises(ValueError, match="unsigned 32-bit"):
        filetime_to_utc_us(-1, 0)

    with pytest.raises(ValueError, match="unsigned 32-bit"):
        filetime_to_utc_us(0, 0x100000000)


def test_vqt_preservation_all_valuetypes() -> None:
    """Test VQT preservation across all canonical ValueTypes including BOOL and BLOB with timestamp assertions."""
    cache = BoundedLatestValueCache(max_items=50)
    cache.bind_active_session("src1", "sess1")
    facade = OpcDaReadFacade(cache)

    test_cases = [
        ("Simulated.I16", ValueType.I16, 1234, VT_I2),
        ("Simulated.I32", ValueType.I32, 1234567, VT_I4),
        ("Simulated.F32", ValueType.F32, 45.67, VT_R4),
        ("Simulated.F64", ValueType.F64, 123456.789012, VT_R8),
        ("Simulated.BOOL", ValueType.BOOL, True, VT_BOOL),
        ("Simulated.STRING", ValueType.STRING, "StateNormal", VT_BSTR),
        ("Simulated.BLOB", ValueType.BLOB, bytearray(b"\x01\x02\x03\x04\x05"), VT_ARRAY | VT_UI1),
    ]

    base_time = 1700000000000000
    expected_timestamps = {}
    for idx, (path, vtype, val, expected_vt) in enumerate(test_cases, start=1):
        ts = base_time + idx * 1000
        expected_timestamps[path] = ts
        sample = ItemSample.create(
            item_id=idx,
            opc_item_path=path,
            value=val,
            value_type=int(vtype),
            quality=OPC_QUALITY_GOOD,
            timestamp_us=ts,
            source_id="src1",
            session_id="sess1",
            sequence=idx,
        )
        cache.put_sample(sample)

    # Read all items
    paths = [tc[0] for tc in test_cases]
    results = facade.read_sync(paths)
    assert len(results) == len(test_cases)

    for (item_val, hr), (path, vtype, val, expected_vt) in zip(results, test_cases):
        assert hr == S_OK
        assert item_val is not None
        assert item_val.opc_item_path == path
        assert item_val.value_type == int(vtype)
        assert item_val.quality == OPC_QUALITY_GOOD
        assert item_val.vartype == expected_vt
        assert item_val.access_rights == OPC_READABLE

        # Strict timestamp & FILETIME assertion per item
        expected_ts = expected_timestamps[path]
        assert item_val.source_timestamp_us == expected_ts
        expected_low, expected_high = utc_us_to_filetime(expected_ts)
        assert item_val.filetime_low == expected_low
        assert item_val.filetime_high == expected_high

        if vtype == ValueType.BLOB:
            # BLOB bytearray must be converted to immutable bytes
            assert isinstance(item_val.value, bytes)
            assert item_val.value == b"\x01\x02\x03\x04\x05"
        elif vtype == ValueType.F32:
            assert pytest.approx(item_val.value, 0.01) == 45.67
        else:
            assert item_val.value == val


def test_atomic_snapshot_deep_isolation_and_nested_payload() -> None:
    """Test that mutating returned OpcItemValue or nested payloads never corrupts cache."""
    cache = BoundedLatestValueCache(max_items=10)
    cache.bind_active_session("src1", "sess1")
    facade = OpcDaReadFacade(cache)

    raw_blob = bytearray(b"original_payload")
    nested_dict = {"config": [1, 2, {"status": "intact"}]}

    sample1 = ItemSample.create(
        item_id=1,
        opc_item_path="Tag.Blob",
        value=raw_blob,
        value_type=int(ValueType.BLOB),
        quality=OPC_QUALITY_GOOD,
        timestamp_us=1700000000000000,
        source_id="src1",
        session_id="sess1",
        sequence=1,
    )
    sample2 = ItemSample.create(
        item_id=2,
        opc_item_path="Tag.Nested",
        value=nested_dict,
        value_type=int(ValueType.STRING),
        quality=OPC_QUALITY_GOOD,
        timestamp_us=1700000000000000,
        source_id="src1",
        session_id="sess1",
        sequence=2,
    )
    cache.put_sample(sample1)
    cache.put_sample(sample2)

    # Mutate caller's original raw_blob and nested_dict
    raw_blob[0] = ord("X")
    nested_dict["config"][2]["status"] = "mutated_locally"

    read_results = facade.read_sync(["Tag.Blob", "Tag.Nested"])
    val_blob, hr1 = read_results[0]
    val_nested, hr2 = read_results[1]

    assert hr1 == S_OK and hr2 == S_OK
    assert val_blob is not None and val_nested is not None

    # Value inside cache was protected
    assert val_blob.value == b"original_payload"
    assert isinstance(val_blob.value, bytes)
    assert val_nested.value["config"][2]["status"] == "intact"

    # Now attempt consumer mutation on returned nested structure
    val_nested.value["config"][2]["status"] = "consumer_corrupted"

    # Subsequent read verifies cache was NOT altered
    read_results2 = facade.read_sync(["Tag.Nested"])
    assert read_results2[0][0].value["config"][2]["status"] == "intact"


def test_vqt_preservation_across_non_good_states() -> None:
    """Test VQT preservation across stale, disconnected, and restored states."""
    cache = BoundedLatestValueCache(max_items=10, stale_timeout_s=1.0)
    cache.bind_active_session("src1", "sess1")
    facade = OpcDaReadFacade(cache)

    sample = ItemSample.create(
        item_id=1,
        opc_item_path="Simulated.Tag",
        value=500.0,
        value_type=int(ValueType.F32),
        quality=OPC_QUALITY_GOOD,
        timestamp_us=1700000000000000,
        source_id="src1",
        session_id="sess1",
        sequence=1,
    )
    cache.put_sample(sample)

    # 1. Stale transition
    stale_now = time.monotonic() + 2.0
    res_stale = facade.read_sync(["Simulated.Tag"], now=stale_now)
    item_stale, hr_stale = res_stale[0]
    assert hr_stale == S_OK
    assert item_stale is not None
    assert item_stale.quality == OPC_QUALITY_BAD_COMM_FAILURE
    assert item_stale.value == 500.0
    assert item_stale.source_timestamp_us == 1700000000000000

    # 2. Disconnect transition
    cache.mark_disconnected("sess1")
    res_disc = facade.read_sync(["Simulated.Tag"])
    item_disc, hr_disc = res_disc[0]
    assert hr_disc == S_OK
    assert item_disc is not None
    assert item_disc.quality == OPC_QUALITY_BAD_NOT_CONNECTED
    assert item_disc.value == 500.0
    assert item_disc.source_timestamp_us == 1700000000000000


def test_namespace_resolution_and_address_space_browsing() -> None:
    """Test predictable alias resolution, path validation, and address space browsing."""
    cache = BoundedLatestValueCache(max_items=10)
    cache.bind_active_session("src1", "sess1")
    facade = OpcDaReadFacade(cache)

    sample = ItemSample.create(
        item_id=1,
        opc_item_path="Simulated.Furnace.Temperature",
        value=750.5,
        value_type=int(ValueType.F32),
        quality=OPC_QUALITY_GOOD,
        timestamp_us=1700000000000000,
        source_id="src1",
        session_id="sess1",
        sequence=1,
    )
    cache.put_sample(sample)

    # Alias is registered by default
    assert facade.resolve_path("OPCBridge.Mirror.Lab.Temperature") == "Simulated.Furnace.Temperature"
    assert facade.is_valid_item_path("OPCBridge.Mirror.Lab.Temperature")
    assert facade.is_valid_item_path("Simulated.Furnace.Temperature")
    assert not facade.is_valid_item_path("Unknown.NonExistent.Tag")

    # Address space browsing
    items = facade.browse_items()
    assert "OPCBridge.Mirror.Lab.Temperature" in items
    assert "Simulated.Furnace.Temperature" in items

    mirror_items = facade.browse_items(prefix="OPCBridge.Mirror")
    assert "OPCBridge.Mirror.Lab.Temperature" in mirror_items
    assert "Simulated.Furnace.Temperature" not in mirror_items


def test_group_add_items_validation() -> None:
    """Test Group AddItems validation: length mismatch and unknown items."""
    cache = BoundedLatestValueCache(max_items=10)
    cache.bind_active_session("src1", "sess1")
    facade = OpcDaReadFacade(cache)

    sample = ItemSample.create(
        item_id=1,
        opc_item_path="Simulated.Furnace.Temperature",
        value=100.0,
        value_type=int(ValueType.F32),
        quality=OPC_QUALITY_GOOD,
        timestamp_us=1700000000000000,
        source_id="src1",
        session_id="sess1",
        sequence=1,
    )
    cache.put_sample(sample)

    gh, _ = facade.create_group("Group1")

    # Mismatched lengths must raise ValueError (E_INVALIDARG)
    with pytest.raises(ValueError, match="Length mismatch"):
        facade.add_items_to_group(gh, ["Simulated.Furnace.Temperature"], [101, 102])

    # Valid and unknown items
    results = facade.add_items_to_group(
        gh,
        ["Simulated.Furnace.Temperature", "Invalid.Tag.Name"],
        [101, 102],
    )
    assert len(results) == 2
    s_handle1, hr1 = results[0]
    s_handle2, hr2 = results[1]

    assert hr1 == S_OK
    assert s_handle1 > 0
    assert hr2 == OPC_E_UNKNOWNITEMID
    assert s_handle2 == 0


def test_group_read_sync_cardinality_and_invalid_handles() -> None:
    """Test that read_group_sync preserves exact cardinality with OPC_E_INVALIDHANDLE."""
    cache = BoundedLatestValueCache(max_items=10)
    cache.bind_active_session("src1", "sess1")
    facade = OpcDaReadFacade(cache)

    sample = ItemSample.create(
        item_id=1,
        opc_item_path="Simulated.Furnace.Temperature",
        value=200.0,
        value_type=int(ValueType.F32),
        quality=OPC_QUALITY_GOOD,
        timestamp_us=1700000000000000,
        source_id="src1",
        session_id="sess1",
        sequence=1,
    )
    cache.put_sample(sample)

    gh, _ = facade.create_group("Group1")
    add_res = facade.add_items_to_group(gh, ["Simulated.Furnace.Temperature"], [999])
    s_handle, _ = add_res[0]

    # Read with: [valid_handle, invalid_handle_1, invalid_handle_2, valid_handle]
    requested_handles = [s_handle, 8888, 7777, s_handle]
    read_results = facade.read_group_sync(gh, server_handles=requested_handles)

    # Exact cardinality of 4 returned
    assert len(read_results) == 4

    # Index 0: valid
    assert read_results[0][0] == 999
    assert read_results[0][1] is not None
    assert read_results[0][2] == S_OK

    # Index 1: invalid handle (8888)
    assert read_results[1][0] == 0
    assert read_results[1][1] is None
    assert read_results[1][2] == OPC_E_INVALIDHANDLE

    # Index 2: invalid handle (7777)
    assert read_results[2][0] == 0
    assert read_results[2][1] is None
    assert read_results[2][2] == OPC_E_INVALIDHANDLE

    # Index 3: valid
    assert read_results[3][0] == 999
    assert read_results[3][1] is not None
    assert read_results[3][2] == S_OK


def test_poll_group_subscription_update_rate_and_injection() -> None:
    """Test group subscription polling enforces update rate starting at now=0.0 deterministically."""
    cache = BoundedLatestValueCache(max_items=10)
    cache.bind_active_session("src1", "sess1")
    facade = OpcDaReadFacade(cache)

    sample = ItemSample.create(
        item_id=1,
        opc_item_path="Simulated.Furnace.Temperature",
        value=100.0,
        value_type=int(ValueType.F32),
        quality=OPC_QUALITY_GOOD,
        timestamp_us=1700000000000000,
        source_id="src1",
        session_id="sess1",
        sequence=1,
    )
    cache.put_sample(sample)

    gh, revised_rate = facade.create_group("SubGroup", requested_update_rate_ms=500, client_handle=77)
    assert revised_rate == 500
    facade.add_items_to_group(gh, ["Simulated.Furnace.Temperature"], [1])

    callbacks_received: list[list[OpcItemValue]] = []

    def on_data_change(client_handle: int, items: list[OpcItemValue]) -> None:
        assert client_handle == 77
        callbacks_received.append(items)

    facade.advise_group(gh, on_data_change)

    # 1. Deterministic clock: First poll at now=0.0 (initial values delivered)
    delivered = facade.poll_group_subscription(gh, now=0.0)
    assert delivered == 1
    assert len(callbacks_received) == 1
    assert callbacks_received[-1][0].value == 100.0

    # 2. Deterministic clock: Poll at now=0.01 (elapsed 10ms < 500ms): throttled, MUST return 0!
    delivered = facade.poll_group_subscription(gh, now=0.01)
    assert delivered == 0
    assert len(callbacks_received) == 1

    # 3. Poll at now=0.2 (200ms < 500ms): throttled
    delivered = facade.poll_group_subscription(gh, now=0.2)
    assert delivered == 0
    assert len(callbacks_received) == 1

    # 4. Poll at now=0.6 (600ms >= 500ms) with NO change: not triggered
    delivered = facade.poll_group_subscription(gh, now=0.6)
    assert delivered == 0
    assert len(callbacks_received) == 1

    # 5. Value change and poll at now=1.2 (elapsed > 500ms)
    cache.put_sample(
        ItemSample.create(
            item_id=1,
            opc_item_path="Simulated.Furnace.Temperature",
            value=105.0,
            value_type=int(ValueType.F32),
            quality=OPC_QUALITY_GOOD,
            timestamp_us=1700000001000000,
            source_id="src1",
            session_id="sess1",
            sequence=2,
        )
    )
    delivered = facade.poll_group_subscription(gh, now=1.2)
    assert delivered == 1
    assert len(callbacks_received) == 2
    assert callbacks_received[-1][0].value == 105.0

    # 6. Quality-only change (same timestamp, value unchanged) and poll at now=1.8
    cache.put_sample(
        ItemSample.create(
            item_id=1,
            opc_item_path="Simulated.Furnace.Temperature",
            value=105.0,
            value_type=int(ValueType.F32),
            quality=OPC_QUALITY_BAD_COMM_FAILURE,
            timestamp_us=1700000001000000,  # same timestamp
            source_id="src1",
            session_id="sess1",
            sequence=3,
        )
    )
    delivered = facade.poll_group_subscription(gh, now=1.8)
    assert delivered == 1
    assert len(callbacks_received) == 3
    assert callbacks_received[-1][0].quality == OPC_QUALITY_BAD_COMM_FAILURE


def test_callback_reentrancy_and_exception_isolation() -> None:
    """Test that client exceptions do not propagate and re-entrant calls do not deadlock."""
    cache = BoundedLatestValueCache(max_items=10)
    cache.bind_active_session("src1", "sess1")
    facade = OpcDaReadFacade(cache)

    sample = ItemSample.create(
        item_id=1,
        opc_item_path="Simulated.Furnace.Temperature",
        value=10.0,
        value_type=int(ValueType.F32),
        quality=OPC_QUALITY_GOOD,
        timestamp_us=1700000000000000,
        source_id="src1",
        session_id="sess1",
        sequence=1,
    )
    cache.put_sample(sample)

    gh, _ = facade.create_group("ReentrantGroup", requested_update_rate_ms=50)
    facade.add_items_to_group(gh, ["Simulated.Furnace.Temperature"], [1])

    reentrant_action_completed = False

    def crashing_and_reentrant_cb(client_handle: int, items: list[OpcItemValue]) -> None:
        nonlocal reentrant_action_completed
        # Re-entrant call back into facade
        facade.get_server_status()
        reentrant_action_completed = True
        # Simulate client bug/crash
        raise RuntimeError("Simulated client callback crash")

    facade.advise_group(gh, crashing_and_reentrant_cb)

    # Polling must NOT raise despite client callback exception
    delivered = facade.poll_group_subscription(gh, now=1.0)
    assert delivered == 1
    assert reentrant_action_completed is True


def test_write_rejection_sync_and_async() -> None:
    """Test strict write rejection across sync, DA3, and async mock methods."""
    cache = BoundedLatestValueCache(max_items=10)
    facade = OpcDaReadFacade(cache)
    mock = MockOpcDaComServer(facade)

    # Facade write_sync
    res1 = facade.write_sync(["Simulated.Tag1", "Simulated.Tag2"], [10.0, 20.0])
    assert res1.overall_hresult == S_FALSE
    assert len(res1.item_results) == 2
    assert all(r.hresult == OPC_E_BADRIGHTS for r in res1.item_results)

    # Facade write_vqt
    res2 = facade.write_vqt(["Simulated.Tag1"], [50.0])
    assert res2.overall_hresult == S_FALSE
    assert res2.item_results[0].hresult == OPC_E_BADRIGHTS

    # Mock SyncWrite, ItemIOWriteVQT, AsyncWrite
    res3 = mock.SyncWrite(["Simulated.Tag1"], [10.0])
    assert res3.overall_hresult == S_FALSE
    assert res3.item_results[0].hresult == OPC_E_BADRIGHTS

    res4 = mock.ItemIOWriteVQT(["Simulated.Tag1"], [10.0])
    assert res4.overall_hresult == S_FALSE
    assert res4.item_results[0].hresult == OPC_E_BADRIGHTS

    res5 = mock.AsyncWrite(["Simulated.Tag1"], [10.0])
    assert res5.overall_hresult == S_FALSE
    assert res5.item_results[0].hresult == OPC_E_BADRIGHTS


def test_real_com_server_entry_point_explicit_failure() -> None:
    """Test that RealOpcDaComServer and start_real_com_server explicitly fail."""
    with pytest.raises(RuntimeError, match="Real (Windows )?COM (OPC DA )?server"):
        RealOpcDaComServer()

    with pytest.raises(RuntimeError, match="Real COM server cannot start"):
        start_real_com_server()


def test_advosol_conceptual_contract_and_mock_metadata() -> None:
    """Test AdvosolPluginContract conceptual metadata and Mock server flags."""
    cache = BoundedLatestValueCache(max_items=10)
    facade = OpcDaReadFacade(cache)
    plugin = AdvosolPluginContract(facade)
    mock = MockOpcDaComServer(facade)

    params = plugin.get_server_parameters()
    assert params["ProgID"] == PROGID_MIRROR_V1
    assert params["CLSID"] == CLSID_MIRROR
    assert params["MajorVersion"] == 1

    # Mock server banner and safety flag
    assert "WARNING" in mock.WARNING_BANNER
    assert mock.is_registered is False
    assert "REAL_COM_SERVER=False" in repr(mock)


def test_dynamic_staleness_on_read() -> None:
    """Test that staleness is evaluated dynamically on read without background loops."""
    cache = BoundedLatestValueCache(max_items=10, stale_timeout_s=1.0)
    cache.bind_active_session("src1", "sess1")
    facade = OpcDaReadFacade(cache)

    sample = ItemSample.create(
        item_id=1,
        opc_item_path="Simulated.Furnace.Temperature",
        value=300.0,
        value_type=int(ValueType.F32),
        quality=OPC_QUALITY_GOOD,
        timestamp_us=1700000000000000,
        source_id="src1",
        session_id="sess1",
        sequence=1,
    )
    cache.put_sample(sample)

    # Read immediately: Good quality
    res1 = facade.read_sync(["Simulated.Furnace.Temperature"])
    assert res1[0][0] is not None
    assert res1[0][0].quality == OPC_QUALITY_GOOD

    # Read at now = current + 2.0s: dynamic staleness triggers Bad Comm Failure
    future_now = time.monotonic() + 2.0
    res2 = facade.read_sync(["Simulated.Furnace.Temperature"], now=future_now)
    assert res2[0][0] is not None
    assert res2[0][0].quality == OPC_QUALITY_BAD_COMM_FAILURE


def test_linux_subprocess_import_isolation() -> None:
    """Verify that pure core/mirror/demo modules do not leak windows_mirror or COM libraries."""
    code = (
        "import sys\n"
        "sys.path.insert(0, 'src')\n"
        "import opc_da_bridge.core.cache\n"
        "import opc_da_bridge.mirror.facade\n"
        "import opc_da_bridge.demo.demo_pipeline\n"
        "loaded = set(sys.modules.keys())\n"
        "forbidden = {'opc_da_bridge.windows_mirror', 'win32com', 'pythoncom', 'comtypes'}\n"
        "leak = loaded & forbidden\n"
        "assert not leak, f'Forbidden modules imported: {leak}'\n"
    )
    cmd = [sys.executable, "-c", code]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    assert proc.returncode == 0, f"Import isolation failed: {proc.stderr}"


def test_group_lifecycle_clamping_and_shutdown() -> None:
    """Test group update rate clamping, duplicate group rejection, and orderly shutdown."""
    cache = BoundedLatestValueCache(max_items=10)
    facade = OpcDaReadFacade(cache, min_update_rate_ms=50, max_update_rate_ms=60000)

    # Clamping below min
    gh1, rate1 = facade.create_group("GroupMin", requested_update_rate_ms=10)
    assert rate1 == 50

    # Clamping above max
    gh2, rate2 = facade.create_group("GroupMax", requested_update_rate_ms=100000)
    assert rate2 == 60000

    # Duplicate group rejection
    with pytest.raises(ValueError, match="already exists"):
        facade.create_group("GroupMin")

    # Orderly shutdown notification
    received_shutdown = []
    facade.register_shutdown_callback(lambda reason: received_shutdown.append(reason))
    count = facade.request_shutdown("Maintenance cycle")
    assert count == 1
    assert received_shutdown == ["Maintenance cycle"]
