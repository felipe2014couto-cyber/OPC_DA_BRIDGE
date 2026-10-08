"""Platform-neutral OPC DA Mirror package.

Exposes contracts, official HRESULTs, and OpcDaReadFacade wrapping BoundedLatestValueCache.
"""
from __future__ import annotations

from opc_da_bridge.mirror import contracts, hresult
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
    filetime_to_utc_us,
    utc_us_to_filetime,
    valuetype_to_vartype,
)
from opc_da_bridge.mirror.facade import OpcDaReadFacade, OpcGroup
from opc_da_bridge.mirror.hresult import OPC_E_BADRIGHTS, S_FALSE, S_OK

__all__ = [
    "contracts",
    "hresult",
    "OpcDaReadFacade",
    "OpcGroup",
    "OpcItemValue",
    "OpcServerStatus",
    "OpcItemWriteResult",
    "OpcWriteResult",
    "OpcGroupConfig",
    "utc_us_to_filetime",
    "filetime_to_utc_us",
    "valuetype_to_vartype",
    "OPC_E_BADRIGHTS",
    "S_OK",
    "S_FALSE",
    "OPC_DS_CACHE",
    "OPC_DS_DEVICE",
    "OPC_READABLE",
    "OPC_STATUS_RUNNING",
]
