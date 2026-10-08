# OPC-DA-Bridge Core Architecture

## 1. Overview
The **OPC-DA-Bridge** project provides a platform-neutral, low-latency mirroring solution for industrial OPC DA telemetry.
The pipeline transports process data from a remote or local industrial source across a versioned TLS transport into a **Bounded Latest-Value Cache** at the destination, which provides a read-only presentation contract. Access from OPC DA COM clients requires the future Windows SDK integration.

```
[ Industrial Source ]
        │ (OPC DA / Simulation)
        ▼
[ Source Collector ]
        │ (Monotonic Session Sequence)
        ▼
[ Versioned TLS Transport (v2 Framing) ]
        │ (Server TLS Validation / Token Auth; mTLS Configurable)
        ▼
[ Destination Receiver ]
        │ (Bounded Queue)
        ▼
[ Bounded Latest-Value Cache ]
        │ (Thread-Safe Direct In-Memory Access)
        ▼
[ OpcDaReadFacade ] ────► [ MockOpcDaComServer (Test Mock) ]
        │
        ▼ (Future Windows Phase)
[ Local IPC / Hosting Boundary ]
        ▼
[ Advosol DANSrv Out-of-Process Server ] ────► [ PI Interface for OPC DA (opcint.exe) ]
```

### 1.1 Transport Security & Authentication Model
The default transport implementation and demonstration validate the server's TLS certificate (verifying certificate authority and hostname) and authenticate connecting agents using a SHA-256 shared-token hash exchange over TLS, without requiring client certificates.
Full mutual TLS (mTLS) is supported as an optional configuration when the deployment environment configures server and client SSL contexts with client certificate chains and mutual verification (`cert_reqs=ssl.CERT_REQUIRED`).

---

## 2. Bounded Latest-Value Cache Principles

### 2.1 Current State Cache, NOT Historical Recovery
The destination cache is strictly designed to reflect **current operational state**, not historical time-series storage.
- Storage capacity is bounded (`max_items`, default 10,000 tags) using deterministic LRU eviction.
- Active source state is bounded (`max_sources`, default 1,000 sources) to prevent unbounded memory growth from churn.

### 2.2 Fencing & Monotonic Sequencing
- **Explicit Session Fencing**: Telemetry samples are accepted only from explicitly bound, active sessions. Retired or unbound sessions are rejected.
- **Monotonic Sequence Enforcement**: Sequence ordering is validated session-globally. Duplicates are handled idempotently; out-of-order packets are rejected.

### 2.3 Stale & Disconnect Handling
- **Stale Expiration**: When telemetry stops arriving for a tag beyond `stale_timeout_s`, the item transitions to `Bad [Communication Failure]` (`0x18`) while strictly preserving last known value and source timestamp.
- **Disconnect Transition**: When source transport disconnects, all associated tags transition to `Bad [Not Connected]` (`0x08`), preserving last known value and original source timestamp.
- **Disk Snapshot Non-Good Guarantee**: Cold-start or restart cache state loaded from snapshot disk files is forced to `Bad [Last Known Value]` (`0x14`). Restored items are **never presented as Good** to OPC clients.

---

## 3. OPC DA Mirror Read Facade & Windows Adapter

### 3.1 SDK-Neutral Presentation Facade (`OpcDaReadFacade`)
- Thread-safe wrapper over `BoundedLatestValueCache`.
- Provides predictable aliases (`OPCBridge.Demo.Temperature`, `OPCBridge.Demo.Pressure`) and address space browsing (`browse_items`).
- Evaluates dynamic staleness on read without requiring background loops.
- Emits frozen `OpcItemValue` records with defensive payload copies (`snapshot_batch`), preventing consumer modifications from corrupting the cache. Noncanonical nested payload copies can remain mutable, but do not reference cache state.
- Converts timestamps to Windows FILETIME (100-ns intervals since 1601-01-01 UTC) with uint64 range bounds, mapping Unix epoch (0 us) to `116444736000000000` ticks.
- Maps canonical ValueTypes to COM VARTYPE representations:
  - `I16` -> `VT_I2` (2)
  - `I32` -> `VT_I4` (3)
  - `F32` -> `VT_R4` (4)
  - `F64` -> `VT_R8` (5)
  - `BOOL` -> `VT_BOOL` (11)
  - `STRING` -> `VT_BSTR` (8)
  - `BLOB` -> `VT_ARRAY | VT_UI1` (0x2011)

### 3.2 Strict Read-Only Policy & Official HRESULT Rejection
All client write requests (DA 2.05a `IOPCSyncIO::Write`, DA 3.00 `IOPCSyncIO2::WriteVQT`, `IOPCItemIO::WriteVQT`) are strictly rejected:
- Per-item error code: **`OPC_E_BADRIGHTS = 0xC0040006`** ("Item access rights do not allow the operation").
- Overall method return: **`S_FALSE` (0x00000001)**.
- Under no circumstances does a write request alter cache state or industrial source telemetry.

### 3.3 Group Lifecycle & Async Advise Subscriptions
- Groups negotiate update rates clamped between 50 ms and 60,000 ms.
- Item addition, removal, and synchronous group reads are fully supported, preserving exact 1:1 cardinality with `OPC_E_INVALIDHANDLE` for invalid handles.
- Python mock subscriptions modeling the future `IConnectionPoint::Advise` contract evaluate data changes on group poll with deterministic rate limiting (`None` sentinel) and quality-only change detection. External callbacks are executed outside locks to guarantee re-entrancy and exception isolation.
- Python shutdown callbacks model the future `IOPCShutdown::ShutdownRequest` notification; no COM clients are connected in this stage.

### 3.4 Isolated Windows Adapter & Explicit Mock (`opc_da_bridge.windows_mirror`)
- The real server entry point checks `sys.platform == 'win32'`; the pure Python mock remains importable across platforms. Core has zero COM dependencies.
- Defines proposed ProgID `OPCBridge.Mirror.1` and CLSID `{B7A45C89-4D3B-4921-9E9A-8F5D890E71E2}` (not registered on host).
- Separate real COM server entry point (`RealOpcDaComServer`, `start_real_com_server`) that checks platform and explicitly fails citing lack of licensed SDK runtime; never modifies registry.
- Exposes `AdvosolPluginContract` as a conceptual Python sketch (C# cannot directly call Python in-memory objects without a defined local IPC/hosting boundary).
- Exposes `MockOpcDaComServer` for testing:
  - Explicitly logs and identifies itself as a test mock, **NOT a real COM server**.
  - Operates safely on Linux and Windows without Windows Registry or COM registration.

---

## 4. Protocol Versioning (v2)
- **Framing**: Binary little-endian over TLS.
  - Magic: `0x4F50` ("OP").
  - Version: `0x0002`.
  - Header (16 bytes): `magic (u16) | version (u16) | msg_type (u16) | flags (u16) | seq (u32) | payload_len (u32)`.
  - Payload: variable length.
  - Trailer (4 bytes): CRC32 checksum over header + payload.
- **Message Types**:
  - `0x01` (`HELLO`) / `0x02` (`HELLO_ACK`): Handshake and session ID negotiation.
  - `0x03` (`AUTH`) / `0x04` (`AUTH_ACK`): SHA-256 token authentication.
  - `0x09` (`HEARTBEAT`): Keepalive with timestamp.
  - `0x0D` (`DATA_STREAM`): Multi-tag streaming payload carrying `session_id`, `sequence`, and `items[]`.
  - `0x0E` (`DISCONNECT_NOTICE`): Graceful disconnection notification.
- **Connection Identity Binding**: Each connection strictly binds `HELLO.agent_id` and the server-issued `session_id`. Incoming `DATA_STREAM` payloads must match both fields; mismatches are rejected and logged without enqueueing.
