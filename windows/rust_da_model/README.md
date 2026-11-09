# Rust DA lab model

This crate contains an independent data model and a protocol-neutral service
core for the proposed Rust DA server. It has no OPC Foundation, Windows, COM,
or third-party dependencies. It is not a COM server and cannot be activated by
an OPC DA client.

The model exposes two stable item IDs:

- `RustLab.Numeric.Double` (`f64`)
- `RustLab.Boolean` (`bool`)

It starts at `BadNoSample`; valid samples require the expected type and a
nonzero source FILETIME. Missing or invalid timestamps leave the timestamp
unset and produce an Uncertain model quality; no local/current time is
substituted. The value is retained when its timestamp is absent or invalid.
Disconnect changes quality to `BadNotConnected` while preserving the last
value and source timestamp. Reconnection does not restore Good until a new
sample arrives. Every write attempt is rejected.

`ServerCore` adds an in-process flat browse list, group/item handles, per-item
read results, explicit read-only write rejection, and data-change batches on
Rust channels. Source loss notifies active groups with `BadNotConnected`;
reconnection alone does not notify Good, and publishing a fresh sample does.
The stored group update rate is clamped to 1 ms but no timer, deadband, or
polling scheduler is implemented. Subscribers must map to COM connection
points/callbacks in a future adapter. Error values are internal and are not
HRESULTs; values and timestamps are not marshaled as `VARIANT` or `FILETIME`
structures here.

`LabSimulator` deterministically changes the Double and Boolean values and
accepts an explicit timestamp for each sample. Its disconnect/reconnect path
is exercised by unit tests; it is only a source for the in-process core.

The FILETIME is stored as the original 100 ns ticks since 1601-01-01 UTC. A
future Windows adapter must mark the timestamp `Invalid` if its conversion or
range validation fails. This crate does not convert timestamps to local time.

Run tests and, when the isolated toolchain described in the repository report
is available, cross-build with:

```bash
scripts/test_rust_da_model.sh
```

No OPC DA quality-code or HRESULT mapping is implemented here; that belongs in
a later adapter after the interface and license review.

The isolated COM adapter experiment is documented in
[`docs/RUST_DA_COM_LAB.md`](../../docs/RUST_DA_COM_LAB.md). Its current Win7
launch is blocked by missing Windows DLL imports; the model remains usable and
tested independently.
