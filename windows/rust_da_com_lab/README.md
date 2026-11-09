# Rust OPC DA COM laboratory prototype

Current status (2026-11-09): [consolidated laboratory report](../../docs/ATUALIZACOES_LAB_2026-11-09.md). The historical browse-only boundary below predates the DA 2 callback implementation; see [current interfaces and tests](../../docs/RUST_DA_COM_LAB.md) for Refresh2, AsyncRead and subscriptions. Generated third-party bindings and binaries are not included.

This isolated prototype adapts the existing `opc-da-lab-model` to the server
adapter in Ronbb/rust_opc revision
`da44a99265f9706618bbe506c98f714f2aa46f0b`. It publishes only the simulated
items `RustLab.Numeric.Double` (`VT_R8`) and `RustLab.Boolean` (`VT_BOOL`).
Writes return the OPC bad-rights result. The simulator updates values each
second and accepts source loss through the marker file named by
`RUST_DA_LAB_SOURCE_FLAG` (default: `rust-da-source-offline.flag`). Removing
the marker resumes sampling; quality returns to Good on the next sample.
Logs are appended to `rust-da-com-lab.log` in the process working directory.

## Current implementation boundary

The pinned adapter implements COM `IOPCServer`, `IOPCItemMgt`,
`IOPCGroupStateMgt`, `IOPCSyncIO`, `IOPCCommon`, and
`IOPCBrowseServerAddressSpace`. The legacy two-tag fixture remains flat.
Project-backed destinations expose a hierarchy derived from the dotted Item
IDs, with a separate browse position for each server COM object. OPC_BRANCH
lists immediate folders, OPC_LEAF their leaves, and OPC_FLAT full Item IDs
recursively. GetItemID resolves a leaf to its unchanged full ID. UP above root
returns E_FAIL without moving. Its `IEnumString` supports `Next`, `Skip`, `Reset`, and
`Clone`; access paths are empty. Group/item enumerators, DA 3.0 `IOPCBrowse`,
asynchronous I/O, COM connection points, and `IOPCDataCallback` are not
implemented. The new browse interface passed an in-process COM self-test on
RB1PI2, but the PI OPC Client GUI has not yet been retested against this build.

The executable has no persistent registration command. `/RegServer` exits
without writing the registry. `/Embedding` registers its class object only for
the process lifetime. The chosen laboratory CLSID is
`{7A2F45B1-64D3-4E8C-9A11-3C852B7F2140}`. The separate manual laboratory
registration on RB1PI2 maps `RustDaBridge.Lab.1` to build6; browse-build2 has
not been registered.

## Build

The build script requires the isolated toolchain under `/tmp/opc-rust-toolchain`
and a separate checkout of the pinned source. It creates a temporary copy of
the required workspace crates under the build directory and applies the browse
adapter patch there; it does not change the checkout or copy generated bindings
into this repository. Example:

```bash
OPC_RUST_BINDINGS_CHECKOUT=/tmp/opc-rust-audit-da44a992 \
  scripts/build_rust_da_com_lab.sh
```

The artifact is emitted under a fresh `/tmp/opc-da-com-lab-build.*` directory.
Use it only as an internal lab artifact while source-derivation and
redistribution terms for the OPC Foundation IDL/bindings remain unconfirmed.
Do not publish it or include it in a release package.

For the browse-only in-process COM check, run:

```text
rust-da-com-lab-browse.exe --browse-self-test
```

This initializes COM and exercises `IOPCBrowseServerAddressSpace` and
`IEnumString`; it does not register a persistent ProgID/CLSID or start the
local-server mode.

## Validation status

The browse build is a PE32 i386 executable for `i686-win7-windows-gnu`. Its
hash is recorded in `docs/RUST_DA_COM_LAB.md`. It ran the browse-only COM
self-test on RB1PI2 Windows 7 SP1 x86 with exit code 0. This validates the
interface implementation in-process; it does not establish PI OPC Client
interoperability. The registered path still points to build6, and the browse
build has not been registered or opened by the PI client.
