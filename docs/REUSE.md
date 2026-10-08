# OPC-DA-Bridge Reusable Component Inventory

## Provenance
- **Source Repository**: `OPC-Bridge` (separate source repository; no path published)
- **Source Reference Commit**: `54d7fdfe1e56a4d910980fb1109ba1f2c2b9a757` (reference baseline at task dispatch)
- **Later Observed Source HEAD**: `865eabc2cfb694bc0a1463292dc81c8a323fefcf` (independent branch advance for PI integration; zero source edits made by worker)
- **Target Repository**: `OPC-DA-Bridge`
- **Inspection Date**: October 2026
- **License / Notices**: Proprietary notices preserved from source codebase.

---

## Reused and Adapted Components

| Source File in `OPC-Bridge` | Target File in `OPC-DA-Bridge` | Status | Description & Adaptations |
|-----------------------------|--------------------------------|--------|---------------------------|
| `src/opc_bridge/protocol/messages.py` | `src/opc_da_bridge/protocol/messages.py` | Adapted & Versioned | Reused binary little-endian framing (`MAGIC = 0x4F50`, `<HHHHII` 16-byte header, CRC32 trailer) and handshake structures (`HelloPayload`, `HelloAckPayload`, `AuthPayload`, `AuthAckPayload`, `HeartbeatPayload`). Version incremented to `VERSION = 0x0002` to support versioned streaming. Added `ItemDataUpdate`, `DataStreamPayload` for monotonic telemetry push, and `DISCONNECT_NOTICE`. Preserved backwards-compatible decoding for `VERSION_V1 = 0x0001`. |
| `src/opc_bridge/adapters/base.py` | `src/opc_da_bridge/core/model.py` | Adapted | Reused OPC DA 2.05 standard quality constants (`OPC_QUALITY_GOOD = 0xC0`, `OPC_QUALITY_BAD = 0x00`, `OPC_QUALITY_UNCERTAIN = 0x40`, `OPC_QUALITY_MASK = 0xC0`) and substatuses. Reimplemented into a 100% platform-neutral, pure Python module with zero Windows or COM dependencies. |
| `src/opc_bridge/adapters/simulated.py` | `src/opc_da_bridge/source/simulated_source.py` | Adapted | Reused value generation patterns across canonical types (F64, F32, I32, I16, BOOL, STRING) and adapted them for generating `DataStreamPayload` batches with monotonic sequences and UTC microsecond timestamps. |

---

## Explicitly Excluded Components

To preserve project independence and security boundaries, the following components were intentionally **excluded**:

1. **PI Web API Output**:
   - `src/opc_bridge/server/pi_output.py` and `src/opc_bridge/server/pi_publisher.py` were **completely excluded**.
   - No PI Web API publishing or REST HTTP PI endpoints are included.
2. **Production Secrets, Credentials & Certificates**:
   - No private keys, TLS certificates, connection strings, auth tokens, or passwords copied.
   - All tests and demos generate ephemeral, self-signed test certificates in temporary directories.
3. **Database and Persistence Services**:
   - No PostgreSQL, TimescaleDB, or relational database dependencies (`psycopg2`, SQL scripts, migrations) copied.
   - The destination is a bounded in-memory latest-value cache.
4. **COM and Platform-Specific Windows Modules**:
   - No COM libraries (`win32com`, `pythoncom`), pywin32, Windows service supervisors, or registry scripts imported into core.
   - Core model and cache are completely platform-neutral.
5. **Git, Virtualenvs, Symlinks, and Cache Artifacts**:
   - No `.git` metadata, `.venv`, `.pytest_cache`, `.ruff_cache`, or symlinks copied.
