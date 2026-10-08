# Softing OPC DA Mirror server

This directory reserves the **separate Windows process** boundary for the real OPC DA server. The Python cache and this process communicate over IPv4 loopback only (`127.0.0.1`, JSON Lines protocol v1); the server polls snapshots and keeps the latest sequence per item. Writes are rejected locally and at the OPC DA boundary. The Python core is never imported into COM, and the server must never query the OPC DA Surrogate.

The protocol endpoint is `opc_da_bridge.bridge.server.CacheBridgeServer`; requests are `{"op":"snapshot","items":[...]}` and responses contain item id, value, canonical value type, quality code and label, UTC source timestamp, source-connected and stale flags, and sequence. Sequence regressions must be ignored. A disconnected, stale, or restored value must not be presented as Good.

The server namespace starts with `OPCBridge.Demo.Temperature` and `OPCBridge.Demo.Pressure`. The target is x86 first with a solution configuration prepared for x64. Required OPC DA behavior: browse, groups/items, sync reads, async Advise callbacks, revised update rate, VQT, status, orderly shutdown, and rejection of Write/WriteVQT. The pinned SDK acquisition is described in [THIRD_PARTY.md](../../docs/THIRD_PARTY.md).

**Status:** no C++ server implementation has been compiled or validated yet. COM registration, DCOM, registry, firewall, service installation, and production access are outside this stage. If SDK activation later requires registration, stop and document the exact command, registry keys, privileges, impact, rollback, and lab VM procedure before requesting separate approval.
