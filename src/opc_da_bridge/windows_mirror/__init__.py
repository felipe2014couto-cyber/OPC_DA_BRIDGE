"""Windows integration and adapter package for OPC DA Mirror.

Contains isolated Windows adapter specifications and an explicit Mock for test validation.
"""
from __future__ import annotations

from opc_da_bridge.windows_mirror.adapter import (
    CLSID_MIRROR,
    IS_WINDOWS,
    PROGID_MIRROR_CURR,
    PROGID_MIRROR_V1,
    AdvosolPluginContract,
    MockOpcDaComServer,
)

__all__ = [
    "PROGID_MIRROR_V1",
    "PROGID_MIRROR_CURR",
    "CLSID_MIRROR",
    "IS_WINDOWS",
    "AdvosolPluginContract",
    "MockOpcDaComServer",
]
