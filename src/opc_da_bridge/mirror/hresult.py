"""Official OPC Data Access HRESULT and Error Definitions.

Provides canonical HRESULT status and error codes from the OPC Data Access 2.05a
specification (Appendix A, pp. 176-178).

CRITICAL SPECIFICATION NOTE:
In OPC DA 2.05a, write attempts against read-only items fail with OPC_E_BADRIGHTS (0xC0040006L).
The code 0x0004000FL is OPC_S_INUSE (Success/Information severity 0x0, facility 0x004, code 0x000F).
Informal draft citations sometimes confused this with 0xC004000F.
No error named 'OPC_E_READONLY' exists in the official OPC DA specification.
"""
from __future__ import annotations

# Standard COM HRESULTs
S_OK: int = 0x00000000
S_FALSE: int = 0x00000001
E_FAIL: int = 0x80004005
E_INVALIDARG: int = 0x80070057
E_ACCESSDENIED: int = 0x80070005
E_NOTIMPL: int = 0x80004001
E_OUTOFMEMORY: int = 0x8007000E

# OPC DA Common Error and Success Codes
# (Defined in OPC DA 2.05a Specification, Appendix A, pp. 176-178)
OPC_E_INVALIDHANDLE: int = 0xC0040001        # The value of the handle is invalid
OPC_E_BADTYPE: int = 0xC0040004              # The server cannot convert the data between requested & canonical type
OPC_E_PUBLIC: int = 0xC0040005               # The requested operation cannot be performed on a public group
OPC_E_BADRIGHTS: int = 0xC0040006            # The item's access rights do not allow the operation (WRITE REJECTION)
OPC_E_UNKNOWNITEMID: int = 0xC0040007        # Canonical OPC DA 2.05a Appendix A constant (The item ID is not defined in server)
OPC_E_UNKNOWNITEMNAME: int = OPC_E_UNKNOWNITEMID  # Prototype local alias (non-normative)
OPC_E_INVALIDITEMID: int = 0xC0040008        # The item ID doesn't conform to the server's syntax
OPC_E_INVALIDFILTER: int = 0xC0040009        # The filter string was not valid
OPC_E_UNKNOWNPATH: int = 0xC004000A          # The item's access path is not known to the server
OPC_E_RANGE: int = 0xC004000B                # The value was out of range
OPC_E_DUPLICATENAME: int = 0xC004000C        # Duplicate name not allowed
OPC_S_UNSUPPORTEDRATE: int = 0x0004000D      # The server does not support the requested rate, but revised rate returned
OPC_S_CLAMP: int = 0x0004000E                # A value written to the server was clamped
OPC_S_INUSE: int = 0x0004000F                # The operation cannot be completed because the object is still in use

# Mapping of HRESULT to canonical names and human-readable descriptions
HRESULT_DESCRIPTIONS: dict[int, tuple[str, str]] = {
    S_OK: ("S_OK", "Operation successful"),
    S_FALSE: ("S_FALSE", "Operation partially successful or warning"),
    E_FAIL: ("E_FAIL", "Unspecified failure"),
    E_INVALIDARG: ("E_INVALIDARG", "One or more arguments are invalid"),
    E_ACCESSDENIED: ("E_ACCESSDENIED", "Access denied"),
    E_NOTIMPL: ("E_NOTIMPL", "Method not implemented"),
    E_OUTOFMEMORY: ("E_OUTOFMEMORY", "Ran out of memory"),
    OPC_E_INVALIDHANDLE: ("OPC_E_INVALIDHANDLE", "The value of the handle is invalid"),
    OPC_E_BADTYPE: ("OPC_E_BADTYPE", "The server cannot convert the data between requested and canonical type"),
    OPC_E_PUBLIC: ("OPC_E_PUBLIC", "The requested operation cannot be performed on a public group"),
    OPC_E_BADRIGHTS: ("OPC_E_BADRIGHTS", "Item access rights do not allow the operation (Read-Only)"),
    OPC_E_UNKNOWNITEMNAME: ("OPC_E_UNKNOWNITEMNAME", "The item ID is not defined in the server address space"),
    OPC_E_UNKNOWNITEMID: ("OPC_E_UNKNOWNITEMID", "The item ID is not defined in the server address space"),
    OPC_E_INVALIDITEMID: ("OPC_E_INVALIDITEMID", "The item ID doesn't conform to the server's syntax"),
    OPC_E_INVALIDFILTER: ("OPC_E_INVALIDFILTER", "The filter string was not valid"),
    OPC_E_UNKNOWNPATH: ("OPC_E_UNKNOWNPATH", "The item's access path is not known to the server"),
    OPC_E_RANGE: ("OPC_E_RANGE", "The value was out of range"),
    OPC_E_DUPLICATENAME: ("OPC_E_DUPLICATENAME", "Duplicate group name is not allowed"),
    OPC_S_UNSUPPORTEDRATE: ("OPC_S_UNSUPPORTEDRATE", "Requested rate not supported, revised rate returned"),
    OPC_S_CLAMP: ("OPC_S_CLAMP", "A value written to the server was clamped"),
    OPC_S_INUSE: ("OPC_S_INUSE", "The operation cannot be completed because the object is still in use"),
}


def is_success(hresult: int) -> bool:
    """Return True if HRESULT indicates success (top bit is 0)."""
    return (hresult & 0x80000000) == 0


def is_error(hresult: int) -> bool:
    """Return True if HRESULT indicates failure (top bit is 1)."""
    return (hresult & 0x80000000) != 0


def format_hresult(hresult: int) -> str:
    """Format HRESULT with hex code, name, and description."""
    info = HRESULT_DESCRIPTIONS.get(hresult)
    if info:
        name, desc = info
        return f"{name} (0x{hresult:08X}): {desc}"
    if is_error(hresult):
        return f"ERROR_0x{hresult:08X}"
    return f"SUCCESS_0x{hresult:08X}"
