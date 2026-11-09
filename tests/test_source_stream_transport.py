# SPDX-License-Identifier: MIT
import pytest

from opc_da_bridge.management.sources import SshSourceChannel, SourceError
from opc_da_bridge.management.deployment import AgentError


def test_persistent_source_transport_preserves_hresult_and_rejects_other_transport(tmp_path, monkeypatch):
    channel = SshSourceChannel(tmp_path)
    target = {
        "read_session_transport": "ssh_stream_v1",
        "read_transport": "opc_da_automation",
    }
    monkeypatch.setattr(channel, "stream_request", lambda *args, **kwargs: {
        "ok": False, "hresult": -2147024891,
    })

    with pytest.raises(SourceError) as error:
        channel.request(target, {"operation": "read"})
    assert error.value.hresult == -2147024891

    target["read_transport"] = "rust_lab_loopback_snapshot"
    with pytest.raises(AgentError, match="OPC Automation"):
        channel.request(target, {"operation": "read"})
