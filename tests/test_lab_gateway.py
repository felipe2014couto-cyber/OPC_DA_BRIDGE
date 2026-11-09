from __future__ import annotations

import logging

import pytest

from opc_da_bridge.core.cache import BoundedLatestValueCache
from opc_da_bridge.core.model import OPC_QUALITY_GOOD, OPC_QUALITY_BAD_NOT_CONNECTED, ValueType
from opc_da_bridge.lab.gateway import (
    DaReading,
    Gateway,
    Pywin32DaSource,
    TagConfig,
    da_quality_to_ua,
    infer_value_type,
    is_com_disconnected_error,
    valid_timestamp,
)
from opc_da_bridge.lab.server import validate_endpoint_binding
from opc_da_bridge.lab.stability import _metrics_command


class FakeDa:
    def __init__(self):
        self.connected = False
        self.fail = False
        self.connect_count = 0
        self.item_error = False
        self.value = 12.5

    def connect(self, prog_id):
        assert prog_id == "ABB.AfwOpcDaSurrogate.1"
        self.connected = True
        self.connect_count += 1

    def read(self, item_ids):
        if self.fail:
            raise OSError("simulated DA disconnect")
        return [DaReading(item_ids[0], self.value, OPC_QUALITY_GOOD, None, type(self.value).__name__,
                          "HRESULT 0x80000000" if self.item_error else None)]

    def close(self):
        self.connected = False


def test_dynamic_type_and_timestamp_validation():
    assert infer_value_type(True) == ValueType.BOOL
    assert infer_value_type(123) == ValueType.I16
    assert infer_value_type(50000) == ValueType.I32
    assert infer_value_type(1.2) == ValueType.F64
    assert valid_timestamp(None) is False
    assert valid_timestamp(0) is False
    assert valid_timestamp(1_700_000_000_000_000) is True


def test_missing_timestamp_is_not_fabricated_and_disconnect_is_bad():
    source = FakeDa()
    cache = BoundedLatestValueCache(stale_timeout_s=60)
    gateway = Gateway(source, cache, [TagConfig("Tag.X", "ns=2;s=Tag.X")],
                      "ABB.AfwOpcDaSurrogate.1", logging.getLogger("test-lab"))
    gateway.poll()
    item = cache.get_by_path("Tag.X")
    assert item.value == 12.5
    assert item.value_type == ValueType.F64
    assert item.source_timestamp_us == 0
    assert da_quality_to_ua(item.quality, gateway.connected, item.stale) == 0

    source.fail = True
    gateway.poll()
    item = cache.get_by_path("Tag.X")
    assert item.quality == OPC_QUALITY_BAD_NOT_CONNECTED
    assert item.source_timestamp_us == 0
    assert da_quality_to_ua(item.quality, gateway.connected, item.stale) == 0x80310000


def test_da_quality_major_mapping():
    assert da_quality_to_ua(0xC0) == 0
    assert da_quality_to_ua(0x40) == 0x40000000
    assert da_quality_to_ua(0x00) == 0x80000000


def test_item_read_error_never_keeps_cached_value_good():
    source = FakeDa()
    cache = BoundedLatestValueCache(stale_timeout_s=60)
    gateway = Gateway(source, cache, [TagConfig("Tag.X", "ns=2;s=Tag.X")],
                      "ABB.AfwOpcDaSurrogate.1", logging.getLogger("test-lab"))
    gateway.poll()
    source.item_error = True
    gateway.poll()
    item = cache.get_by_path("Tag.X")
    assert item.value == 12.5
    assert item.quality == 0x00


def test_disconnect_reconnect_uses_new_cache_session():
    source = FakeDa()
    cache = BoundedLatestValueCache(stale_timeout_s=60)
    gateway = Gateway(source, cache, [TagConfig("Tag.X", "ns=2;s=Tag.X")],
                      "ABB.AfwOpcDaSurrogate.1", logging.getLogger("test-lab"))
    gateway.poll()
    first_session = cache.get_by_path("Tag.X").session_id
    source.fail = True
    gateway.poll()
    source.fail = False
    gateway.poll()
    item = cache.get_by_path("Tag.X")
    assert source.connect_count == 2
    assert item.session_id != first_session
    assert item.quality == OPC_QUALITY_GOOD
    assert item.disconnected is False


def test_unsupported_returned_type_is_item_bad_not_source_disconnect():
    source = FakeDa()
    cache = BoundedLatestValueCache(stale_timeout_s=60)
    gateway = Gateway(source, cache, [TagConfig("Tag.X", "ns=2;s=Tag.X")],
                      "ABB.AfwOpcDaSurrogate.1", logging.getLogger("test-lab"))
    gateway.poll()
    source.value = object()
    gateway.poll()
    item = cache.get_by_path("Tag.X")
    assert gateway.connected is True
    assert item.value == 12.5
    assert item.quality == 0x00


def test_known_com_disconnect_hresult_propagates_for_gateway_reconnect():
    class FakeComError(Exception):
        hresult = 0x800706BA  # RPC_S_SERVER_UNAVAILABLE

    class BrokenItem:
        def Read(self, source):
            raise FakeComError("simulated OPC DA server unavailable")

    source = Pywin32DaSource()
    source.item_objects = {"Tag.X": BrokenItem()}

    with pytest.raises(FakeComError):
        source.read(["Tag.X"])
    assert is_com_disconnected_error(FakeComError()) is True
    assert is_com_disconnected_error(OSError("ordinary item issue")) is False


def test_endpoint_security_binding_rules():
    validate_endpoint_binding("127.0.0.1", "local_no_security")
    validate_endpoint_binding("localhost", "local_no_security")
    with pytest.raises(ValueError, match="loopback"):
        validate_endpoint_binding("10.0.0.12", "local_no_security")
    validate_endpoint_binding("10.0.0.12", "sign_and_encrypt")
    with pytest.raises(ValueError, match="wildcard"):
        validate_endpoint_binding("0.0.0.0", "sign_and_encrypt")
    with pytest.raises(ValueError, match="security.mode"):
        validate_endpoint_binding("127.0.0.1", "None")


def test_windows_process_metrics_target_real_gateway_child():
    query = _metrics_command(7380)
    assert "ParentProcessId -eq 7380" in query
    assert "opc_da_bridge.lab.server" in query
    assert "ProcessId,$p.CPU,$p.WorkingSet64" in query
    assert "} }" not in query
