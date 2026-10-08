# OPC-DA-Bridge

Relatório consolidado da etapa 2, comparação dos SDKs e plano do laboratório: [STAGE2_HANDOVER.md](docs/STAGE2_HANDOVER.md).

Independent, platform-neutral OPC DA telemetry bridge, bounded latest-value cache, and read facade.

---

## 1. Project Overview

**OPC-DA-Bridge** provides an industrial telemetry mirroring pipeline designed to replicate process measurements from an industrial source across a secure, versioned TLS transport into a destination node. At the destination, incoming data is ingested into a **Bounded Latest-Value Cache** which maintains current plant state with high fidelity, deterministic sequence ordering, and strict quality semantics.

This codebase delivers:
- **Deliverable 1 (Platform-Neutral Core & Transport Pipeline)**:
  - Pure Python representation of OPC DA canonical data types, values, 16-bit quality words, and microsecond UTC timestamps with zero Windows COM dependencies.
  - Bounded latest-value cache with deterministic LRU eviction, active session fencing, and monotonic sequence validation.
  - Stale and disconnect transitions preserving last known values and original source UTC timestamps.
  - Disk restore safety strictly guaranteeing restored items are never presented as Good quality.
  - Versioned TLS transport (v2 framing) with CRC32 integrity checks, server TLS validation, and token authentication (mTLS configurable).
- **Deliverable 2 (OPC DA Mirror Read Facade & Windows Contracts)**:
  - SDK-neutral `OpcDaReadFacade` exposing a read-only contract for laboratory tests.
  - Predictable namespace: `OPCBridge.Mirror.Lab.Temperature` and `OPCBridge.Mirror.Lab.Pressure` with address space browsing (`browse_items`).
  - Windows FILETIME conversions (100-nanosecond intervals since Jan 1, 1601 UTC) preserving microsecond source timestamps without fabrication (Unix epoch = 0 us maps to 116444736000000000 ticks).
  - Canonical VARTYPE mappings (VT_I2, VT_I4, VT_R4, VT_R8, VT_BOOL, VT_BSTR, and VT_ARRAY|VT_UI1 for BLOB).
  - Strict write rejection for DA2 Write and DA3 WriteVQT returning official HRESULT `OPC_E_BADRIGHTS` (`0xC0040006`) without state mutation.
  - A local, read-only JSON Lines bridge over IPv4 loopback for a separate Windows server process.
  - Group lifecycle, update rate negotiation/clamping, and Advise change subscriptions with quality-only detection.
  - Isolated Windows adapter (`opc_da_bridge.windows_mirror`) with explicit `MockOpcDaComServer` for testing (clearly stating it is NOT a real COM server).

---

## 2. Directory Layout

```
OPC-DA-Bridge/
├── pyproject.toml              # Build metadata and test configuration
├── README.md                   # This documentation
├── docs/
│   ├── ARCHITECTURE.md         # Detailed pipeline, cache, and mirror architecture
│   ├── REUSE.md                # Source provenance and exact reused components
│   ├── DECISIONS_REAL_OPC_DA_MIRROR.md  # Architectural decision record for Windows COM Mirror
│   └── STAGE2_REPORT.md        # Comprehensive Stage 2 evaluation and verification report
├── src/
│   └── opc_da_bridge/
│       ├── core/               # Data model, 16-bit quality classification, bounded cache
│       ├── mirror/             # SDK-neutral read facade, canonical HRESULTs, FILETIME math
│       ├── windows_mirror/     # Isolated Windows adapter, conceptual plugin, explicit mock
│       ├── protocol/           # v2 framing, CRC32, message pack/unpack
│       ├── source/             # Simulated telemetry source
│       ├── destination/        # Destination receiver queue
│       ├── transport/          # Versioned TLS client/server transport
│       └── demo/               # End-to-end runnable demonstration pipeline
└── tests/                      # 50 comprehensive unit and integration tests
```

---

## 3. Test Suite

Run the full cross-platform test suite:

```bash
cd OPC-DA-Bridge
PYTHONDONTWRITEBYTECODE=1 pytest -q -p no:cacheprovider
```

The test suite covers:
- Core data model and encoding roundtrips.
- 16-bit OPC quality word bitmask classification.
- Bounded cache capacity and eviction.
- Stale detection and disconnect quality transition.
- Disk restore non-Good quality guarantee.
- TLS transport handshakes, server certificate validation, shared-token authentication, identity/session binding, and stream delivery.
- Official OPC DA HRESULT error definitions (`OPC_E_BADRIGHTS = 0xC0040006`, `OPC_E_UNKNOWNITEMID = 0xC0040007`, `S_OK`, `S_FALSE`).
- Windows FILETIME conversions and VARTYPE mappings (including BLOB).
- Predictable namespace resolution (`OPCBridge.Mirror.Lab.Temperature`, `OPCBridge.Mirror.Lab.Pressure`) and browsing.
- Read facade sync read, defensive snapshots with deeply copied payloads, and dynamic staleness detection.
- Strict write rejection returning `OPC_E_BADRIGHTS` without state changes.
- Group lifecycle, rate clamping, and Advise change subscriptions with deterministic rate limiting.
- Orderly shutdown notification callbacks.
- Mock COM server behavior across Linux and Windows without COM registration.
- Subprocess Linux import isolation ensuring core/mirror do not leak Windows COM libraries.

---

## 4. Running the End-to-End Demo

To run the complete simulated source -> TLS transport -> destination cache demonstration:

```bash
cd OPC-DA-Bridge
PYTHONPATH=src python3 src/opc_da_bridge/demo/demo_pipeline.py
```

The demo displays:
1. Ephemeral self-signed server TLS certificate generation.
2. Handshake and authentication between client and destination server.
3. Live streaming across F64, F32, I32, BOOL, STRING, and I16 data types.
4. Monotonic sequence verification (resending duplicates and injecting out-of-order packets).
5. Disconnect event propagation: automatic transition of items to `Bad [Not Connected]` preserving last known values and timestamps.
6. Disk snapshot restore: verifying cold-started cache loads items as `Bad [Last Known Value]`.

---

## 5. Engineering Boundaries & Limitations

- **Real COM Server Status**: A real Windows COM server is NOT implemented and NOT validated in the Windows laboratory or with PI Interface. The codebase provides platform-neutral contracts, read facade, conceptual plugin outline, and a software test mock. COM registration was strictly not performed.
- **Local process bridge**: `CacheBridgeServer` binds only to `127.0.0.1` and serves defensive JSON snapshots; it does not expose cache mutation operations. See `windows/softing_mirror_server/README.md`.
- **Softing SDK**: pinned to the immutable commit in `docs/THIRD_PARTY.md`. The acquisition script was not run on this Linux host because PowerShell is unavailable; no SDK content is present in the repository.
- **Advosol Plugin Boundary**: `AdvosolPluginContract` is a conceptual Python sketch. In production Windows deployment, bridging between C# and Python requires a defined local IPC or hosting boundary (e.g. named pipes, local RPC, or Python.NET), which remains a future engineering phase.
- **No Inferred PI Homologation**: While Advosol supports DA 2.05a/3.0 and x86/x64 EXEs, formal compatibility with OSIsoft/AVEVA PI Interface requires testing in an authorized laboratory environment (PI Interface x86 -> Mirror x86 baseline and -> Mirror x64).
- **Tooling Notice**: Ruff is absent from the host environment and was not installed.
- **No System Modifications**: No Windows registry modifications, COM registrations, service installs, firewall rules, or credentials were altered.
- **Commercial Tooling Roadmap**: Full evaluation and deployment procedures for Advosol DANSrv are detailed in `docs/DECISIONS_REAL_OPC_DA_MIRROR.md` and `docs/STAGE2_REPORT.md`.
