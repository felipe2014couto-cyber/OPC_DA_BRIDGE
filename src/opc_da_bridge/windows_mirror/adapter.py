"""Isolated Windows Adapter and Explicit Mock for OPC DA Mirror.

This module provides the integration boundary between the platform-neutral
OpcDaReadFacade and Windows COM / Advosol DANSrv server plugin architectures.

ARCHITECTURAL SAFETY RULES:
1. Zero COM imports in the platform-neutral core.
2. Explicit platform guard (sys.platform == 'win32').
3. Separate real server entry point that verifies platform and explicitly fails
   due to lack of licensed SDK runtime; never modifies registry.
4. Explicit Mock class for testing that prominently declares it is NOT a real COM server.
5. All write attempts strictly return OPC_E_BADRIGHTS (0xC0040006).
"""
from __future__ import annotations

import logging
import sys
from typing import Any, Callable

from opc_da_bridge.mirror.contracts import (
    OPC_DS_CACHE,
    OPC_STATUS_RUNNING,
    OpcItemValue,
    OpcServerStatus,
    OpcWriteResult,
)
from opc_da_bridge.mirror.facade import OpcDaReadFacade
from opc_da_bridge.mirror.hresult import OPC_E_BADRIGHTS, S_FALSE, S_OK

logger = logging.getLogger(__name__)

# --- Server Registration Constants ---
PROGID_MIRROR_V1: str = "OPCBridge.Mirror.1"
PROGID_MIRROR_CURR: str = "OPCBridge.Mirror"
CLSID_MIRROR: str = "{B7A45C89-4D3B-4921-9E9A-8F5D890E71E2}"
SERVER_DESCRIPTION: str = "OPC-DA Bridge Telemetry Mirror Server (Read-Only)"

IS_WINDOWS: bool = (sys.platform == "win32")


class RealOpcDaComServer:
    """Real Windows COM OPC DA Server entry point.

    Strictly verifies platform and fails explicitly due to lack of licensed SDK runtime.
    NEVER modifies Windows registry and NEVER registers COM classes.
    """

    def __init__(self, facade: OpcDaReadFacade | None = None) -> None:
        if sys.platform != "win32":
            raise RuntimeError(
                f"Real Windows COM server cannot run on non-Windows platform '{sys.platform}'."
            )
        raise RuntimeError(
            "Real Windows COM OPC DA server cannot start: "
            "licensed OPC DA SDK runtime is unavailable and COM registration is prohibited."
        )


def start_real_com_server(facade: OpcDaReadFacade | None = None) -> None:
    """Attempt to launch real COM server; verifies platform and explicitly fails."""
    if sys.platform != "win32":
        raise RuntimeError(
            f"Real COM server cannot start on non-Windows platform '{sys.platform}'."
        )
    raise RuntimeError(
        "Real COM server cannot start: licensed OPC SDK runtime is unavailable and COM registration is prohibited."
    )


class AdvosolPluginContract:
    """Conceptual Python outline/sketch for future Advosol DANSrv plugin integration.

    NOTE: This is a conceptual sketch, NOT a verified ABI, NOT verified .NET/C# signatures,
    and NOT an implemented .NET assembly. In production Windows deployment, C# cannot directly
    invoke a Python object without a defined local IPC or hosting boundary (e.g. named pipes,
    gRPC, or Python.NET), which remains a future architectural requirement.
    """

    def __init__(self, facade: OpcDaReadFacade) -> None:
        self.facade = facade

    def get_server_parameters(self) -> dict[str, Any]:
        """Advosol INsPlugin conceptual callback: Return server metadata and registration properties."""
        return {
            "ProgID": PROGID_MIRROR_V1,
            "CLSID": CLSID_MIRROR,
            "Description": SERVER_DESCRIPTION,
            "MajorVersion": 1,
            "MinorVersion": 0,
            "VendorInfo": self.facade.vendor_info,
            "MinUpdateRate": self.facade.min_update_rate_ms,
        }

    def read_items(self, item_paths: list[str]) -> list[tuple[OpcItemValue | None, int]]:
        """Advosol INsPlugin conceptual callback: Query current item values from read facade."""
        return self.facade.read_sync(item_paths)

    def write_items(self, item_paths: list[str], values: list[Any]) -> OpcWriteResult:
        """Advosol INsPlugin conceptual callback: Strictly reject write requests with OPC_E_BADRIGHTS."""
        return self.facade.write_sync(item_paths, values)


class MockOpcDaComServer:
    """Explicit Software Mock simulating OPC DA COM interfaces for offline test verification.

    CRITICAL NOTICE:
    THIS IS A TEST MOCK AND NOT A REAL WINDOWS COM OPC DA SERVER.
    IT DOES NOT REGISTER COM CLASSES, DOES NOT MANAGE COM VTABLES, AND DOES NOT
    ACCEPT DCOM RPC CONNECTIONS FROM EXTERNAL PROCESSES.
    """

    WARNING_BANNER: str = (
        "WARNING: MockOpcDaComServer is an explicit software mock for testing. "
        "It is NOT a real Windows COM OPC DA server. No COM registration or DCOM plumbing is active."
    )

    def __init__(self, facade: OpcDaReadFacade) -> None:
        self.facade = facade
        self.prog_id = PROGID_MIRROR_V1
        self.clsid = CLSID_MIRROR
        self.is_registered = False  # Always False: No Windows Registry modification performed
        logger.info(self.WARNING_BANNER)

    def __repr__(self) -> str:
        return f"<MockOpcDaComServer(ProgID='{self.prog_id}', REAL_COM_SERVER=False)>"

    # --- Simulated IOPCServer Methods ---

    def GetStatus(self) -> OpcServerStatus:
        """Simulate IOPCServer::GetStatus()."""
        return self.facade.get_server_status()

    def AddGroup(
        self,
        name: str,
        active: bool,
        requested_update_rate_ms: int,
        client_handle: int,
    ) -> tuple[int, int]:
        """Simulate IOPCServer::AddGroup(). Returns (server_handle, revised_update_rate_ms)."""
        return self.facade.create_group(
            name=name,
            requested_update_rate_ms=requested_update_rate_ms,
            client_handle=client_handle,
            active=active,
        )

    def RemoveGroup(self, group_handle: int) -> bool:
        """Simulate IOPCServer::RemoveGroup()."""
        return self.facade.remove_group(group_handle)

    # --- Simulated IOPCSyncIO Methods ---

    def SyncRead(
        self,
        group_handle: int,
        server_handles: list[int] | None = None,
        source: int = OPC_DS_CACHE,
    ) -> list[tuple[int, OpcItemValue | None, int]]:
        """Simulate IOPCSyncIO::Read(). Returns list of (client_handle, item_val, hresult)."""
        return self.facade.read_group_sync(group_handle, server_handles)

    def SyncWrite(
        self,
        item_paths: list[str],
        values: list[Any],
    ) -> OpcWriteResult:
        """Simulate IOPCSyncIO::Write(). Strictly rejected with OPC_E_BADRIGHTS."""
        return self.facade.write_sync(item_paths, values)

    def AsyncWrite(
        self,
        item_paths: list[str],
        values: list[Any],
    ) -> OpcWriteResult:
        """Simulate IOPCAsyncIO2::Write(). Strictly rejected with OPC_E_BADRIGHTS."""
        return self.facade.write_sync(item_paths, values)

    # --- Simulated DA 3.0 IOPCItemIO Methods ---

    def ItemIORead(
        self,
        item_paths: list[str],
        max_ages_ms: list[int] | None = None,
    ) -> list[tuple[OpcItemValue | None, int]]:
        """Simulate DA3 IOPCItemIO::Read()."""
        return self.facade.read_item_io(item_paths, max_ages_ms)

    def ItemIOWriteVQT(
        self,
        item_paths: list[str],
        values: list[Any],
        qualities: list[int] | None = None,
        timestamps_us: list[int] | None = None,
    ) -> OpcWriteResult:
        """Simulate DA3 IOPCItemIO::WriteVQT(). Strictly rejected with OPC_E_BADRIGHTS."""
        return self.facade.write_vqt(item_paths, values, qualities, timestamps_us)

    # --- Simulated IOPCShutdown Request ---

    def ShutdownRequest(self, reason: str = "Service shutdown requested") -> int:
        """Simulate IOPCShutdown::ShutdownRequest() delivered to connected clients."""
        return self.facade.request_shutdown(reason)
