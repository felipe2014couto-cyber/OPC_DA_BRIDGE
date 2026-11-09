# Rust OPC DA COM lab prototype

Status checked 2026-11-05. This is an internal laboratory experiment; it is
not certified OPC DA software. No source code or IDL/binding files from OPC
Foundation were copied into the project checkout.

## Preserved model and implementation

The independent model remains in `windows/rust_da_model`. Its current suite
passes 11 tests on Linux. It retains the two items:

| Item ID | COM type | Simulation |
|---|---|---|
| `RustLab.Numeric.Double` | `VT_R8` / `f64` | Starts at 12.5 and increments once per sample. |
| `RustLab.Boolean` | `VT_BOOL` / `bool` | Alternates on each sample. |

The isolated COM adapter is in `windows/rust_da_com_lab`. It composes that
model with the `DaServer` adapter from
[`Ronbb/rust_opc` revision `da44a99265f9706618bbe506c98f714f2aa46f0b`](https://github.com/Ronbb/rust_opc/tree/da44a99265f9706618bbe506c98f714f2aa46f0b).
The adapter routes `IOPCServer`, `IOPCItemMgt`, `IOPCGroupStateMgt`,
`IOPCSyncIO`, `IOPCCommon`, and `IOPCBrowseServerAddressSpace`. Writes return
`OPC_E_BADRIGHTS` (`0xC0040006`). Values carry the simulated sample's FILETIME
and OPC quality. Source loss is controlled by a marker file named by
`RUST_DA_LAB_SOURCE_FLAG`; the default marker is
`rust-da-source-offline.flag` in the process working directory. With the
marker present, the cached values are `OPC_QUALITY_NOT_CONNECTED`; after it is
removed, Good returns only after a fresh simulator sample.

The browse address space is flat: `QueryOrganization` returns
`OPC_NS_FLAT`; `BrowseOPCItemIDs` exposes only the two read-only items and
honors item-type, read-access, and simple `*`/`?` filters. `GetItemID` resolves
the full Item ID, and `BrowseAccessPaths` returns an empty enumerator. Because
the namespace is flat, `ChangeBrowsePosition` returns `E_FAIL` for valid
directions, as required by DA 2.05. Its COM `IEnumString` implements `Next`,
`Skip`, `Reset`, and `Clone`, allocating strings with `CoTaskMemAlloc` for the
client to release. The later `refresh-build1` has passed browse, synchronous
read, and Refresh through the actual PI OPC Client 1.4.0.0 controlled through
its native GUI commands in an SSH session. See the dated results at the end;
the physical click in the user's RDP session still needs confirmation.

`IOPCBrowse` (the newer DA 3.0 interface), group/item enumerators,
`IOPCAsyncIO*`, COM connection points, and `IOPCDataCallback` are still absent.
Asynchronous subscriptions/notifications remain a known interoperability gap.

The executable's laboratory CLSID is
`{7A2F45B1-64D3-4E8C-9A11-3C852B7F2140}`. The executable itself does not write
persistent ProgID/CLSID registration; `/RegServer` exits without changing the
registry. An administrator separately registered `RustDaBridge.Lab.1` in
HKCU and HKLM on RB1PI2. The current `LocalServer32` target is recorded in
the dated registration history below.
`-Embedding` (the spelling COM appends to `LocalServer32`) and `/Embedding`
call `CoRegisterClassObject` for the current process only. The program then
probes class-factory activation and `IOPCServer` QueryInterface. The program
appends plain text events to `rust-da-com-lab.log` in its working directory.

## Windows 7 import correction

The previous PE import table was traced to exact source files in the resolved
dependency graph:

- `api-ms-win-core-winrt-error-l1-1-0.dll` came from
  `windows-result 0.4.1`: `Error::new` called `ErrorInfo::originate_error`,
  which calls `RoOriginateErrorW`. The API requires Windows 8. The crate
  documents `--cfg=windows_slim_errors` for COM applications that do not need
  extended `IErrorInfo`; the build now uses this documented setting, keeping
  HRESULT results while removing that Windows 8 import.
- `combase.dll` had three imported COM APIs: `CoCreateFreeThreadedMarshaler`,
  `CoTaskMemAlloc`, and `CoTaskMemFree`. `windows-implement 0.60.2` generates
  the free-threaded-marshaler query by default for `#[implement]` classes;
  `windows-core 0.62.2` and `windows 0.62.2` supplied the corresponding
  `combase.dll` link names. Microsoft's API references list these functions
  in `Ole32.dll`; `CoTaskMemAlloc` has a Windows 2000 minimum. The build script
  copies only those two MIT/Apache crate sources into its fresh `/tmp` build
  directory and changes the five exact link attributes (three API names,
  including the shared task allocator declarations). It does not modify the
  Cargo cache, the upstream OPC checkout, global toolchain settings, or any
  Windows system DLL.

The resulting PE import table no longer includes `combase.dll` or the WinRT
error API set. Other imports are OS system libraries: `ole32.dll`,
`oleaut32.dll`, `rpcrt4.dll`, `propsys.dll`, `kernel32.dll`, `ADVAPI32.dll`,
`USERENV.dll`, `WS2_32.dll`, `ntdll.dll`, and `msvcrt.dll`. `propsys.dll` was
confirmed present on RB1PI2.

## Provenance and license status

- The Rust crates in the pinned repository declare MIT in their manifests.
  That notice applies to those declared crate sources; it does not determine
  the terms of the third-party IDL files.
- The pinned repository's `.metadata/opcda.idl` and
  `.metadata/opccomn.idl` headers identify OPC Foundation copyright, state
  “ALL RIGHTS RESERVED”, and refer to the License Grant in the specification.
  Its `NOTICE` also calls out OPC Foundation material. The `build.rs` uses
  these files to generate COM declarations. The generated declarations are
  therefore kept in an external checkout, isolated behind this adapter, and
  are not checked into this project.
- The OPC Foundation's [Classic Data Access page](https://opcfoundation.org/developer-tools/specifications-classic/data-access/)
  labels the DA specification as governed by its Specifications Agreement and
  lists access as “Paid Members”. The current
  [Specifications Agreement v1.15](https://opcfoundation.org/license/specifications/1.15/)
  defines an Authorized Licensee as a party obtaining the specification
  through the Foundation's access/download process, including acceptance. It
  describes internal reproduction/use reasonably necessary to implement, and
  prohibits copying and redistributing the specification. We have no evidence
  in this checkout that the organization obtained the DA specification or
  accepted the applicable terms. Membership status alone is not inferred here.
- The Foundation's [Source Deliverables Agreement](https://opcfoundation.org/license/source/1.11/)
  says terms can differ among files in one repository and directs users to
  each file's header. Thus the wrapper's MIT declaration does not establish
  the IDL/bindings terms.

This is a provenance record, not a legal opinion or a claim that the IDL or
generated bindings license is resolved. Development is continuing for the
user-authorized internal lab scope. Do not publish or redistribute the source,
generated bindings, or the linked executable. The browse-build2 executable was
copied to a separate folder on RB1PI2 only for the in-process interface test;
it was not registered.

## Reproducing the cross-build

Prerequisites are the isolated toolchain described in
`RUST_DA_POC_FEASIBILITY.md` and an external Git checkout at the pinned commit.
The build script verifies the revision and asks
`scripts/patch_rust_opc_da_browse.py` to copy the required workspace crates to a
fresh temporary directory and apply the isolated browse adapter patch there.
The checkout and generated bindings are not modified or copied into this
project. The OPC IDL/bindings license questions below remain unchanged.

```bash
OPC_RUST_BINDINGS_CHECKOUT=/tmp/opc-rust-audit-da44a992 \
  scripts/build_rust_da_com_lab.sh
```

The browse artifact was built by Rust `nightly-2026-11-03`
(`1.101.0-nightly`, LLVM 23.1.5) and MinGW-w64 GCC
`13.2.0-6ubuntu1+26.1`, for `i686-win7-windows-gnu`. It was PE32 i386; its
final path and SHA-256 are recorded below.

## Tests and current blocker

| Check | Result |
|---|---|
| Independent model on Linux | 11 passed, 0 failed. |
| Cross-build of COM adapter | Passed with `scripts/build_rust_da_com_lab.sh`; Rust nightly `1.101.0-nightly`; `windows 0.62.2`, `windows-core 0.62.2`, `windows-result 0.4.1`, `windows-implement 0.60.2`. Upstream emitted deprecation warnings for `Atomic::fetch_update`. |
| PE architecture/import inspection | PE32 i386; final SHA-256 `054368ad911022ff9420c53e4ab78000a8bacb39e2c050f8739406495eeccfc8`; no imports for `combase.dll` or WinRT error API sets. |
| RB1PI2 identity | `RB1PI2`, Windows 7 Professional SP1, 32-bit, version `6.1.7601`. |
| Direct launch on RB1PI2 | Final file copied to `D:\OPCB\rust-da-com-lab-20261104-build6\rust-da-com-lab.exe`; `certutil` hash matched. Informational mode exited 0. |
| Volatile COM lifecycle | `--self-test` exited 0: initialized MTA; registered class object in process; `CoGetClassObject`, `IClassFactory::CreateInstance`, `IOPCServer` QueryInterface; explicitly revoked class object and verified it was no longer retrievable. |
| COM service calls | In-process COM client created group and added the two items; synchronous reads returned `12.5`/`true`, types `VT_R8`/`VT_BOOL`, Good quality and nonzero FILETIME. Writes returned `OPC_E_BADRIGHTS`. Disconnect returned NotConnected quality; reconnect alone stayed bad; a fresh sample restored Good. All checks passed. |
| Local-server command mode | Started with `-Embedding`, remained running and passed its startup COM probe; then only that test process was stopped. |
| Build6 PI OPC Client result | User confirmed server discovery and Group1 at 1000 ms; browse showed “This server does not support browsing”; group had 0 items and no read occurred. |
| Browse build cross-build | Passed for `i686-win7-windows-gnu`; PE32 i386, SHA-256 `e089232d2d7972f4523c632d668f2a8e5df376d3434bc164fdbe3162322deb9f`. Imports include `ole32.dll`/`oleaut32.dll`; `combase.dll` and WinRT API sets are absent. |
| Browse interface self-test | Passed on RB1PI2 Windows 7 SP1 x86, exit 0, from `D:\OPCB\rust-da-com-lab-20261104-browse-build2\rust-da-com-lab-browse.exe --browse-self-test`. It exercised QueryInterface, flat namespace, `ChangeBrowsePosition`, both Item IDs through `IEnumString` `Next`/`Clone`/end/`Reset`/`Skip`, `GetItemID`, and empty `BrowseAccessPaths`. No persistent class registration was made by this test. |
| Browse build in PI OPC Client | At this historical checkpoint build6 was registered; see the 2026-11-05 follow-up below for the current build7 target and pending GUI validation. |
| Initial pre-registration conflict query | Before the first per-user registration, read-only checks found no HKCU/HKCR entries for the candidate ProgID or CLSID. |
| Registration on RB1PI2 | User-authorized entries exist in HKCU and HKLM for ProgID `RustDaBridge.Lab.1`, CLSID `{7A2F45B1-64D3-4E8C-9A11-3C852B7F2140}`, `LocalServer32`, and OPC DA 2.0 category. At this historical checkpoint both values pointed to diagnostic build6; see the 2026-11-05 follow-up. |
| External COM activation | `System.__ComObject` activation through the ProgID succeeded; process ID 5068 was left running for the manual PI OPC Client test. |
| Saved state | Original registration files remain in `D:\OPCB\rust-da-com-lab-20261104-build6`. The pre-change LocalServer32 exports are `D:\OPCB\rust-da-com-lab-20261104-browse-build2\hkcu-build6-before-browse-build2.reg` and `hklm-build6-before-browse-build2.reg`; `rollback-to-build6.cmd` imports only these two keys. |
| PI OPC Client | Build6 was found and connected far enough to create `Group1`; browse failed, no items were added, and no read occurred. Browse-build2 remains untested in the GUI. |

The `i686-win7-windows-gnu` target name alone did not guarantee Windows 7
compatibility. The local import correction resolved this launch failure; the
final binary executed and passed COM interface calls on RB1PI2. No DLLs were
copied to the machine.

The registration used for the PI client is present in both
`HKCU\Software\Classes` and `HKLM\Software\Classes`. Each scope has the
ProgID `RustDaBridge.Lab.1`, CLSID
`{7A2F45B1-64D3-4E8C-9A11-3C852B7F2140}`, `LocalServer32`, and
`Implemented Categories\{63D5F432-CFE4-11D1-B2C8-0060083BA1FB}` (OPC DA 2.0).
At that checkpoint both `LocalServer32` values pointed to diagnostic build6; COM appends
`-Embedding`. Browse has passed in-process and cross-process COM tests; PI GUI
interoperability is still pending. Async callbacks remain missing. The saved
pre-registration files and restricted rollback scripts are in the build6
laboratory folder.

After registration, the PI OPC Client still requires a manual RDP session for
discovery/connect/browse/read checks; SSH does not control its GUI.

## PI OPC Client Sync and Flat browse investigation (2026-11-04)

At the start of the investigation, the registered server was browse-build2 in
both HKCU and HKLM. Its running process on RB1PI2 was PID 3132, launched by the
COM Service Control Manager in session 2 with `-Embedding`. The build2 log in `C:\Windows\System32` contained
only startup messages: the old build logged neither `AddItems` nor `Read`, so
the reported blank Sync cannot be attributed to the server method from that
run. Its working-directory-relative log path also made the file difficult to
find. No registry values were changed during this investigation.

For a real cross-process check without registering another CLSID, a fresh
diagnostic executable published the COM object under the temporary item
moniker `RustDaBridge.Lab.IpcDiagnostic` in the Running Object Table. A separate
client process retrieved that object, forcing COM interprocess marshaling, and
then called the browse, group, item, and synchronous-read interfaces. This
exercises the COM proxy/stub boundary, but it does not test SCM activation by
ProgID/CLSID or the PI GUI.

| Check on RB1PI2 | Result |
|---|---|
| Distinct client/server processes | Passed: client PID 1280 obtained the object marshaled from server PID 4816. |
| Browse through COM proxy | Passed: flat namespace and both item IDs; `IEnumString` `Next`, `Clone`, end-of-enumeration, `Reset`, and `Skip` completed. The server log shows the request, 2-item result, and each enumeration call. |
| AddGroup/AddItems | Passed: both items returned per-item success (`S_OK`), server handles 1 and 2, canonical types `VT_R8` (5) and `VT_BOOL` (11), readable access. |
| Synchronous Read | Passed: per-item results were `S_OK`; simulated samples were `VT_R8` 22.5 and `VT_BOOL` true, quality `0xC0` (Good), with the same nonzero FILETIME source timestamp. The displayed numeric sample changes with simulator timing. |
| Advise/write | Not called. |
| Graceful diagnostic shutdown | Passed using the test marker; ROT registration was revoked and PID 4816 exited. |

The executable used for this cross-process run is
`D:\OPCB\rust-da-com-lab-20261104-diag-build6\rust-da-com-lab-diag.exe`,
SHA-256 `4844497742a7cc251333b26dba50373175aa6d547c9bb07cdc3246688b50dc70`.
It is a separate PE32 x86 build. The diagnostic executable also logs method requests, per-item results,
browse enumerator calls, and errors to `rust-da-com-lab.log` beside the
executable, tagged with the process ID. This makes it possible to tell whether
the PI client reached `AddItems`/`Read` and what the server returned once this
build is active.

One early version of the test client passed a null string to
`ChangeBrowsePosition`. The generated COM proxy rejected it with
`0x800706F4` (RPC null reference) before the server method ran; this was a
test-harness parameter issue, not evidence of a server browse failure. The
test now passes an allocated empty string and the full cross-process browse
passes. The PI client's actual browse arguments remain unknown until its calls
are captured in the diagnostic build.

The cross-process read confirms that the server's synchronous read path,
VARIANT values, item handles, quality, and FILETIME can cross COM correctly in
the diagnostic test. It does not prove the PI OPC Client uses the same source,
handles, or client-side display path. Likewise, the successful enumerator test
does not identify why the PI window appeared stuck in Flat mode. Those two
observations require a PI GUI run against this diagnostic build and inspection
of its adjacent log. After the user closed the PI OPC Client, both PID 3700
(client) and PID 3132 (build2 server) terminated naturally. No force stop was
needed. The two authorized HKCU/HKLM `LocalServer32` default values were then
updated to diagnostic build6; no other registration value was changed.
Read-only exports of the prior HKCU/HKLM `LocalServer32` subkeys were saved beside build6 as
`rollback-hkcu-localserver32.reg` (SHA-256
`e2d95c33f3be633490171add240795f454f463ab52535e5275997b71a136d8a5`) and
`rollback-hklm-localserver32.reg` (SHA-256
`7e64e73038522c647787066239ca9c02c813bb945cbb4c8a6b7ee9a3660c2872`). Both
restore the quoted build2 path. Importing these two exports restores the prior
values. At the last check after the switch, neither build2 nor the diagnostic
server had been launched. The user must reopen the PI OPC Client in the RDP
session to test the registered diagnostic build.

## Flat browse retry fix (2026-11-05)

The PI log for server PID 4724 showed repeated calls with direction `1` and an
empty destination. In OPC DA 2.05, direction `1` is `OPC_BROWSE_UP`. The server
reported its namespace as `OPC_NS_FLAT`, whose browse position is the flat
root; the adapter has no hierarchical position state. Build6 incorrectly
returned `S_OK` for `UP` without changing position. The PI client therefore
treated the call as successful and retried against the unchanged root, which
matches the repeated log entries. Clause 4.4.8.2 says `ChangeBrowsePosition`
returns `E_FAIL` for a FLAT space and also when moving above the root. [OPC DA
2.05 specification, §4.4.8.2](https://www.advosol.com/OpcSpecs/opcda205_cust.pdf);
the OPC Foundation identifies DA 2.05a as the released Classic Data Access
specification [on its DA specifications page](https://opcfoundation.org/developer-tools/specifications-classic/data-access/).

The temporary dependency patch now logs the requested direction, destination,
current `flat-root` position, and returned HRESULT. For valid direction values
UP/DOWN/TO it returns `E_FAIL` (`0x80004005`) without changing the flat
namespace. Invalid directions return `E_INVALIDARG`. The test-client browse
sequence models the PI retry behavior: it repeats `UP` only after `S_OK`, then
asserts that the first `E_FAIL` stops the loop after one call. It next browses
with `OPC_FLAT` and checks the two enumerated Item IDs.

| Build7 cross-process check on RB1PI2 | Result |
|---|---|
| `ChangeBrowsePosition(UP)` retry sequence | Passed: 1 call, `E_FAIL` (`0x80004005`), flat position remains root; retry loop terminated. |
| Flat browse and enumerator | Passed: `OPC_FLAT` returned `RustLab.Boolean` and `RustLab.Numeric.Double`; `Next`/`Clone`/`Reset`/`Skip`/end-of-enumeration completed across COM processes. |
| AddItems and synchronous Read | Passed: per-item `S_OK`; `VT_R8` value 23.5 and `VT_BOOL` false; both quality `0xC0` (Good) and shared nonzero FILETIME source timestamp. No write or Advise was called. |
| Shutdown | Passed: the diagnostic server was stopped normally via its marker; no registry registration was used by this test. |

The corrected PE32 x86 executable is
`D:\OPCB\rust-da-com-lab-20261105-diag-build7\rust-da-com-lab-diag.exe`,
SHA-256 `8c46c85341d2d63f30a3c02cc73c3d2e0987aafff9367651cce7c1e9c053a5d3`.
Before the authorized switch, the exact prior LocalServer32 keys
were exported to `rollback-before-build7-hkcu.reg` (SHA-256
`60355107d01f67a7249ad7ee473d67573002e4fc4e6afcd7b16fa910ea806161`) and
`rollback-before-build7-hklm.reg` (SHA-256
`b04414bb7b9c06a9a4c9de02a3d834d598abd67ece1f1fb40da3c97332bc804c`) in the
build7 folder. They both restore build6.

## Build7 registration and PI GUI follow-up (2026-11-05)

After the user closed the PI OPC Client, a fresh read-only process query found
no `OPCClient.exe` or lab server process. The RB1PI2 identity was confirmed as
Windows 7 Professional SP1, version 6.1.7601, 32-bit. The build7 executable
hash and both rollback export hashes matched the values above. The two
authorized registry values were still the quoted build6 path before the
change. Only the default string value in these two keys was changed:

```text
HKCU\Software\Classes\CLSID\{7A2F45B1-64D3-4E8C-9A11-3C852B7F2140}\LocalServer32
HKLM\Software\Classes\CLSID\{7A2F45B1-64D3-4E8C-9A11-3C852B7F2140}\LocalServer32
```

Both now point to the quoted build7 executable. No process needed termination.
Read-back confirmed the two values; a subsequent process query found neither
the PI client nor the lab server running. No build7 activation by the PI client
has happened yet, and the build7 log contains only the earlier cross-process
diagnostic run. Thus Flat browse and synchronous reads are proven across COM
processes, but remain unverified in the PI OPC Client GUI.

To test in the RDP session, start PI OPC Client, refresh its local server list,
connect to `RustDaBridge.Lab.1`, choose Flat in Add Item, add
`RustLab.Numeric.Double` and `RustLab.Boolean` to the group, and invoke Sync
(not Advise). Confirm that both rows show values, types (`VT_R8` and
`VT_BOOL`), Good quality, and timestamps. Do not attempt writes. If the dialog
hangs or Sync shows no data, capture the last 100 lines of
`D:\OPCB\rust-da-com-lab-20261105-diag-build7\rust-da-com-lab.log` and the
exact displayed client error. The log entries `[BROWSE_CHANGE_POSITION_RESULT]`,
`[BROWSE_ENUM_RESULT]`, `[ADD_ITEM_RESULT]`, and `[READ_RESULT]` distinguish
browse, per-item AddItems, and synchronous read outcomes. A blank log or no
new PID means the client did not activate this registered executable.

To roll back, close the PI OPC Client and wait for the lab process to exit,
then import the two saved exports from an elevated command prompt:

```bat
reg import "D:\OPCB\rust-da-com-lab-20261105-diag-build7\rollback-before-build7-hkcu.reg"
reg import "D:\OPCB\rust-da-com-lab-20261105-diag-build7\rollback-before-build7-hklm.reg"
```

These exports restore only the previous CLSID `LocalServer32` values, which
point to build6; they do not roll back other registration data.

## PI OPC Client read attempt after build7 registration (2026-11-05)

The installed PI OPC Client manual
(`D:\AppData\PIPC\PI OPC Tools\PI_OPCClient\PI_OPCClient.docx`, chapter 9,
“Reading Data”, pp. 25–26)
specifies: select the connected server and group, choose the source with
`Cache/Device`, then click `SyncRead` for the synchronous operation. `AsyncRead`
is a distinct operation and `Refresh` is asynchronous. The earlier message
“OPC DA v2.05a doesn't support AsyncRead from Cache” therefore does not show
that `IOPCSyncIO::Read` failed; it indicates the asynchronous cache-read path
was selected. Build7 intentionally does not implement async interfaces.

The current RB1PI2 log shows the PI client PID 4240 activated build7 PID 5148,
created groups `DummyGroupABC` and `Group1`, browsed both leaves, and added
`RustLab.Boolean` and `RustLab.Numeric.Double` successfully (`S_OK`, canonical
`VT_BOOL`/`VT_R8`). The log ends with those `ADD_ITEM_RESULT` entries and has no
`READ_REQUEST` or `READ_RESULT`. The service logs `READ_REQUEST` at entry to
its `DaGroupService::read` method, so this attempt did not reach its synchronous
read path. It is not evidence of a failed value marshaling or item handle in
the server. The separate COM cross-process Sync Read test remains successful.

For the GUI retry, select the server, select `Group1` in the Groups list, make
the item rows visible and select one or both, choose `Device` in `Cache/Device`,
and press the explicit `SyncRead` control. Repeat with source `Cache` if useful.
Do not choose `AsyncRead`, `Refresh`, `Advise`, or a write command. Confirm
`READ_REQUEST` followed by one `READ_RESULT` per selected item. The current
log does not yet show that call, so PI OPC Client synchronous reading remains
unverified. No source change, build, process termination, or Registry change
was made for this diagnostic step.

## IOPCSyncIO boundary instrumentation build8 (2026-11-05)

Because the prior `READ_REQUEST` log was emitted only after input validation
and dispatch into `DaGroupService::read`, its absence could not distinguish a
client that never invoked SyncRead from a call rejected earlier in the COM
adapter. A diagnostic hook was added to the temporary pinned-binding patch:
`IOPCSyncIO::Read ENTER` logs the raw DA source enum, count, and null status of
its input/output pointers before validation; `IOPCSyncIO::Read RETURN` records
the adapter HRESULT. The hook calls a default no-op diagnostic method on the
group service; the lab implementation writes it to the existing text log.
This does not log or alter values and does not add asynchronous interfaces.

The isolated cross-compile succeeded with nightly `2026-11-03` for
`i686-win7-windows-gnu`. Build8 is PE32 i386, SHA-256
`8e84d3eb1b81264bf924fddebd11b190e549fb605995b3e95c6d96c937a98435`, at
`D:\OPCB\rust-da-com-lab-20261105-diag-build8\rust-da-com-lab-diag.exe`.
Its import table contains only Windows system DLLs. On RB1PI2, the Flat browse
self-test passed and the distinct-process ROT test passed browse, AddItems,
and `IOPCSyncIO::Read`. The boundary log showed `ENTER source=2 count=2`,
`READ_REQUEST source=Device`, `READ_RESULT` for both items with per-item
`S_OK`, and `RETURN hresult=S_OK`; values were `22.5` (`VT_R8`) and `true`
(`VT_BOOL`), both quality `0xC0` with the same nonzero FILETIME. No Write or
Advise was called. This confirms dispatch and COM marshaling for the test
client only, not for PI OPC Client.

The `--self-test` lifecycle check still reported that class-object revocation
did not take effect (`0x8000FFFF`) while the PI OPC Client remained active and
restarted build7 under the same CLSID. The lifecycle check is unresolved; do
not treat the browse and ROT read tests as a pass for that revocation check.

Before switching, the build7 values were exported to
`rollback-before-build8-hkcu.reg` (SHA-256
`f3d8a04b426b4274d8652798e43c9256f61d76fa98eb0d02ed08674eee57fc28`) and
`rollback-before-build8-hklm.reg` (SHA-256
`f54fffbcc82441b51bc2730eaad92c9624095df6cbf6e2671bad3e43cea5fe88`). Each
export contains only its CLSID `LocalServer32` key and restores build7. The
user-authorized default values in HKCU and HKLM now point to build8. The
build7 server PIDs 5148, 4868, and later 5988 were each path/argument-checked
before being force-stopped; no other processes were terminated. The PI OPC
Client PID 4240 remains active; graceful close failed because it had no main
window, and `taskkill` without `/F` refused to terminate it. It was not
force-stopped. No build8 instance has yet been started by PI OPC Client, so
the actual PI SyncRead result remains pending.

For the next GUI attempt, close/reopen the PI client from RDP, reconnect to
`RustDaBridge.Lab.1`, create an active group, add both tags, select the group,
choose `Device`, and click the manual's explicit `SyncRead` control. The manual
also documents **OPC Server Properties** after connecting; inspect its OPC
Group interface list for `IOPCSyncIO`. If the new log has no `IOPCSyncIO::Read
ENTER`, report that interface-property result and the exact control used. An
entry proves the COM method was dispatched; a return without `READ_REQUEST`
points to adapter validation; `READ_REQUEST` plus per-item results proves the
server's value path. Do not use AsyncRead, Refresh, Advise, or writes.

## Actual PI OPC Client SyncRead passed (2026-11-05)

The installed PI OPC Client was driven through its own native window controls
in SSH session 0. Its OPC Server Properties dialog reported IOPCSyncIO support.
After a Flat browse and Add Selected of the two simulated items, the client's
real Sync command (control 1028, source control 1029 set to Device) entered
build8's `IOPCSyncIO::Read`. The original RDP-session trace for PID 2836 was
not used as a successful read: it still ended in AddItems, and old PID 4104
test reads were excluded by taking only log lines added by the current action.

In the reproducible run PI Client PID 5988 called build8 server PID 5952; the
client's item cells contained `FALSE`/`VT_BOOL` and `61.500000`/`VT_R8`, both
`Good - 11000000`. The same server PID logged per-item S_OK, quality 0xC0,
FILETIME 134383586988495406, and a final interface HRESULT S_OK. Both the
start-a-new-client helper mode and its attach-to-existing-client mode passed.
No Write or Advise operation was called. This is an actual PI executable test
with UI control text inspected, not merely the Rust COM test client or a
screenshot/observation of the user's RDP desktop.

`scripts/test_pi_opc_client_sync.ps1` reproduces the operation and can attach
to the client in the user's RDP session. It strictly verifies the client
version/path/session, selected Rust lab server, group and two Item IDs before
invoking Sync, and uses only newly added log events and their verified server
PID. In this tested version, no selected item rows means Sync reads both group
items; selecting both also worked. Therefore item-row selection alone does not
explain the earlier silent attempt. The exact original RDP command-dispatch
failure remains unidentified, but unavailable IOPCSyncIO or failed marshalling
was not reproduced in the actual client. A new server build was unnecessary.

The helper uses existing Windows PowerShell/.NET only for native UI diagnostic
interop; no tools were installed. See `PI_OPC_CLIENT_TEST.md` for commands,
results and the full process-cleanup record. All own diagnostic clients and
servers were cleaned up; stale RDP server PID 2836 was path/argument-checked
and stopped after confirming no PI client remained. The final process query
was empty for PI Client/Rust lab server, and both LocalServer32 values remain
the unchanged build8 path.

## Refresh2 laboratory implementation (2026-11-05)

Build8 could not service PI Client Refresh: the client reported “No
ConnectionPointContainer found.” The isolated build patch now includes
`windows/rust_da_com_lab/src/refresh_adapter.rs`, implementing a laboratory
`IConnectionPointContainer`, a single `IOPCDataCallback` connection point,
and `IOPCAsyncIO2::Refresh2`. The group service exposes its active server and
client handles. Refresh requests are read using the existing model and returned
on a separately COM-initialized thread. The Global Interface Table marshals the
callback interface between COM apartments. The PI client registers and
unregisters the connection automatically; no PI Client Advise button or write
operation was used. Quality and source FILETIME are preserved by the same read
path used for Sync; invalid/missing timestamps are not replaced with now.

This is deliberately partial: asynchronous Read, Write and Cancel2 return
E_NOTIMPL; connection enumerators and GetConnectionPointContainer are not
implemented; continuous unsolicited data-change notifications and historical
replay do not exist. The worker currently defers callbacks by 50 ms, which is
not a formal guarantee about ordering under arbitrary scheduling. These limits
must be addressed and retested before broader interoperability or production.
OPC Foundation IDL/binding license questions remain as documented above; the
new adapter source is kept isolated and the build continues to patch a
temporary copy for internal laboratory use.

The cross-build for `i686-win7-windows-gnu` passed, producing PE32 x86 SHA-256
`619cb824f3c2d365a09df01a89f433642f48afe61d194af842283e63ba6dea1a` with
Windows system DLL imports. The actual PI Client 1.4.0.0, in a separate process
on RB1PI2, successfully used the default Cache setting and Refresh after normal
registered COM activation. The callback returned two per-item S_OK results and
the PI cells showed their values, quality and source timestamps. A separate
volatile-class test exercised Good → Bad → Bad → Good by interrupting only the
simulated source; the two Bad callbacks had the same source FILETIME, then a
new sample restored Good with a later FILETIME. Sync regression passed. The
model suite passed 11 tests on Linux; the Win7 x86 test binaries were built.

The registered executable is
`D:\OPCB\rust-da-com-lab-20261105-refresh-build1\rust-da-com-lab.exe`.
Only the default LocalServer32 values in HKCU/HKLM were changed, after saving
`rollback-before-refresh-hkcu.reg` and `rollback-before-refresh-hklm.reg` in
that folder. They restore build8; see `PI_OPC_CLIENT_TEST.md` for hashes,
rollback commands, GUI instructions and actual process/test evidence.
`scripts/test_pi_opc_client_refresh.ps1` reproduces either the isolated
source-loss test or, with `-Registered`, the normal activation and one-click
Refresh test. No PI Client or lab server was running at the last status query.

## Server shutdown connection point (2026-11-05)

The PI Client's connection warning was caused by the server object not
implementing its DA 2.05 shutdown connection point. The group already exposed
`IConnectionPointContainer` for `IOPCDataCallback`; that does not satisfy the
separate server-level `IOPCShutdown` IID
`{F31DFDE1-07B6-11D2-B2D8-0060083BA1FB}`. The isolated binding patch now adds
`IConnectionPointContainer` to the server object and accepts the client's
`IOPCShutdown` subscription through `FindConnectionPoint`, `Advise`, and
`Unadvise`, retaining the callback in the Global Interface Table. The actual
PI Client connected without displaying “Shutdown notification not supported
by this server”; the test log confirmed `SHUTDOWN_CALLBACK_CONNECT`.

This addition suppresses the unsupported warning by accepting the standard
subscription. The laboratory server does not currently initiate
`IOPCShutdown::ShutdownRequest` before process exit, and the shutdown connection
point's enumeration methods return `E_NOTIMPL`; these parts remain partial.
The data group's existing connection-point enumeration and unsolicited
continuous notifications also remain incomplete. Refresh and synchronous
read remain available.

The new PE32 x86 build was cross-compiled for `i686-win7-windows-gnu` with the
isolated toolchain and tested against PI OPC Client 1.4.0.0 on RB1PI2. SHA-256:
`622fdc4d1e6755f761d77062778ef7feef08a82c231ac3cff35daa459a2ecc5e`. Both a
volatile-class run and normal registered activation opened without the warning;
the registered run showed `FALSE` (`VT_BOOL`) and `45.500000` (`VT_R8`), both
Good, with timestamp `11/05/26 11:16:54`, and passed Refresh and Sync.
The volatile run also retained the existing Good → Bad → Bad → Good simulated
source test. No user data, ABB, RSLinx or PI Interface was used.

Only the default `LocalServer32` values for the laboratory CLSID in HKCU and
HKLM now point to
`D:\OPCB\rust-da-com-lab-20261105-shutdown-build1\rust-da-com-lab.exe`.
Before changing them, the previous refresh-build1 values were exported to
`rollback-before-shutdown-hkcu.reg` and `rollback-before-shutdown-hklm.reg`
inside that folder. Their SHA-256 values are respectively
`23c69f97c7ad718c7ec52d794d3dec8f488d1bbbbe20bc9d35c3fb4d67aa18cd` and
`887c78306fea408fba16eb7ee5221a64eab00990469954082c71558c21e5829e`.
Import those two files to restore the refresh-build1 activation. The previous
executables were preserved.

## Refresh, Async Read and continuous Advise (2026-11-09)

This update supersedes the earlier Refresh2-only limitations. It remains an
internal laboratory prototype, with the pinned Ronbb revision
`da44a99265f9706618bbe506c98f714f2aa46f0b`, windows/windows-core 0.62.2 and the
isolated `nightly-2026-11-03` / `i686-win7-windows-gnu` cross-toolchain. No new
crate, runtime, proprietary build tool, Windows service, firewall or DCOM
setting was installed or changed. The OPC Foundation IDL/derived-binding
licensing questions remain unresolved; generated bindings remain in temporary
build copies and this work was not published externally.

The reported disappearing PI Client row was accompanied by actual
`REFRESH_REQUEST` and `READ_RESULT` events in PID 8488. Each item returned
S_OK, quality 0x40 and FILETIME 0; the subsequent client callback returned
`0x80010105` (RPC_E_SERVERFAULT). Thus the failure occurred after the source
read, in the callback into PI Client. No PI crash dump was collected; the trace
associates the client exception with the missing-timestamp payload, and does
not prove the exact statement failing inside the proprietary client.

The Kepware source timestamp was independently checked through native
`IOPCSyncIO::Read`: both laboratory tags returned Good/192, S_OK and a nonzero
UTC FILETIME. In two comparisons, Automation's Unspecified date interpreted as
UTC differed from the subsequent native read by 0.093–0.140 seconds; interpreting
it as source-local time differed by approximately 10,800 seconds. Only the
LB3PI1 read-agent identity was changed from `reject_unspecified` to `utc`.
The default for unverified sources is still `reject_unspecified`. This preserves
the supplied source time (with Automation's millisecond rounding), and does
not replace it with receipt time. The destination then received Good/192 and
real source timestamps without restarting PID 8488.

The COM projection now rejects an absent/zero/out-of-range source timestamp
with per-item E_FAIL (0x80004005), rather than reporting S_OK with FILETIME 0.
The independent cache still retains its explicit nullable timestamp and quality.
Successful samples with a valid timestamp preserve their original quality,
including Uncertain. An already failing source HRESULT is preserved. This
avoids claiming a successful DA 2 timestamp where none can be supplied; the
PI Client GUI response to the explicit per-item failure remains a separate
interoperability check.

Implemented laboratory paths:

| Operation | Behavior |
| --- | --- |
| IOPCSyncIO::Read | Cache/Device, existing per-item values/types/quality/time/errors. |
| IOPCAsyncIO2::Refresh2 | Active items in an active group, OnDataChange with the request transaction ID; Cache/Device. |
| IOPCAsyncIO2::Read | Explicit handles, including inactive items; OnReadComplete, per-item immediate invalid-handle errors, valid items only in completion. DA 2 Read has no Cache/Device argument. |
| IOPCAsyncIO2::Cancel2 | Cancels a request still queued; OnCancelComplete, no read completion for an accepted cancellation. Unknown/already-dispatched cancellation fails. |
| IConnectionPoint::Advise | Initial OnDataChange and subsequent value/quality/error changes, transaction 0, respecting group/item activity and update rate. A timestamp-only change does not generate a value-change notification. |
| SetEnable/GetEnable | Disable/enable unsolicited transaction-0 events; explicit Read/Refresh completions continue. |
| Client writes | Remain denied. Async Write returns per-item OPC_E_BADRIGHTS, S_FALSE and no pending write. No OPC write was exercised in this validation. |

The group adapter shares its current group state with the subscription worker;
it does not recreate groups or handles for updates. GIT marshals the callback
between COM apartments. Unadvise revokes the GIT registration; workers hold weak
subscription references and stop when the registration/group disappears.
Pending requests are limited to 64 per group and Async Read to 4096 handles.
Each request has a cancellation state so cancellation and completion have one
winner. A 50 ms callback deferral remains a laboratory scheduling choice, not a
formal ordering guarantee under arbitrary load. There is one callback sink per
group; connection enumerators, CloneGroup, DA 1 asynchronous interfaces,
nonzero percent-deadband semantics and DA 3 are not complete. This is not a DA
conformance, load or production qualification.

For the mirror, both Cache and Device currently read the last sample received
by the relay; Device does not synchronously fetch Kepware. The relay owns source
polling and the existing stale timeout. No history, replay or recovery of all
intermediate events is provided.

### Build and tests actually executed

```bash
CARGO_TARGET_DIR=/tmp/opc-da-async-target-20261109 \
  bash scripts/build_rust_da_com_lab.sh
```

Final PE32 i386 artifact SHA-256:
`7f9c7b730ec9359bbaaf65a41116e06212c769abd8ecd378c7024ef4f6d4bc12`.
The import table contains Windows 7 system DLLs, with no combase.dll or WinRT
error DLL. The executable ran on RB1PI2 Windows 7 SP1 x86. XP, x64 COM,
PI Interface and production remain unvalidated.

Five Windows unit tests passed: nullable timestamp projection, fragmented local
HTTP body, stable auth token, concurrent token creation and invalid-token
preservation. The test harness SHA-256 is
`aa7239eb03f5129d6087379bb2e6bc2e49068a5e33ef177bef2f100d2b829a3a`.

The final distinct-process simulated fixture passed on RB1PI2:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File D:\OPCB\rust-da-async-20261109-build3\test_rust_da_async_runtime.ps1 -LaboratoryDirectory D:\OPCB\rust-da-async-20261109-build3 -ExpectedSha256 7f9c7b730ec9359bbaaf65a41116e06212c769abd8ecd378c7024ef4f6d4bc12
```

It used a transient separate CLSID, server PID 7728 and client PID 4380, both
exiting 0. Evidence is under
`D:\OPCB\rust-da-async-20261109-build3\async-fixture-2ed29b6a54d44481b555c085d4926479`.
It verified Sync Cache/Device, Refresh Cache/Device, Async Read including an
inactive item, partial invalid-handle S_FALSE/OPC_E_INVALIDHANDLE, cancellation,
initial and changing Advise events, group inactivity, SetEnable(false), explicit
Refresh while disabled, and simulated Good→Bad→Good. During Bad, the prior
source FILETIME remained unchanged. This is a real COM callback test across
processes, using simulated tags; it is not a PI Client GUI test.

A second distinct-process test used the configured five-item project and only
the two selected Kepware items. PID 9332 received actual source-agent/relay
samples and the native client observed TESTE2=1.0 (VT_R8 from VT_R4), DIGITAL=true
(VT_BOOL), quality 192, per-item S_OK and FILETIMEs 134387125612820000 and
134387125613290000. Sync, Refresh, Async Read, initial Advise, cancellation,
activity and enable/disable checks passed. This test explicitly did not inject
source loss into Kepware. The server exited normally after the test. An earlier
attempt let the SSH parent finish before the configured test and the fixture
was no longer present; keeping the parent SSH open for the whole test resolved
that test-lifetime issue. No unknown server was activated to compensate.

### Deployment and reversal

The current laboratory LocalServer32 default in both HKCU and HKLM is:

```text
"D:\OPCB\rust-da-async-20261109-build3\rust-da-com-lab.exe"
```

Only those two default values were changed. Original build1 and intermediate
builds remain intact. The project is still revision 32 with five unchanged
Item IDs, SHA-256
`0a20244402267f9f849627631a5022228cffb48fd4ffec2cf45977d7a86f80fc`.
The agent confirmed the final executable hash and a validation process that
stopped normally, leaving `validated_waiting_client_activation`.

PI Client PID 11032 (verified path/session 2) did not accept CloseMainWindow and
was terminated under the existing laboratory authorization. Old Rust PID 8488
(verified build1 path and -Embedding) stopped normally through agent-stop.flag.
No unrelated process was stopped. Only the Linux configurator service was
paused/restarted during deployment. There is no session-0 fixture intentionally
left holding port 49321; reopening PI Client in RDP should activate the new
normal server in the interactive session.

An initial registry script used a read-only RegistryKey handle and failed
before changing a value. The scripts were corrected to open only the existing
laboratory keys with writable handles; no ACL was broadened. Rollback syntax
and original executable availability were checked. The guarded reversal script
is `D:\OPCB\rust-da-async-20261109-build3\rollback-localserver.ps1`, SHA-256
`5dcdf06dc18503f09e1f5d9f71b327147fc7a05f4f418339295078edbe4e056a`.
With clients/the laboratory server stopped, it restores only those defaults to
`D:\OPCB\rust-da-kepware-float-20261109-build1\rust-da-com-lab.exe` and refuses
unexpected current values. Restore the matching Linux agents/deployment-state
files from `.opc-bridge-management/backups/before-async-20261109` while only the
Linux configurator is stopped, then start it again. Do not restore the database
unless intentionally reverting later user edits: the catalog revision was not
changed by this deployment.

To reverse only the verified source timezone policy, the source agent's
`source-identity.before-utc-20261109.json` is preserved beside source-identity.json
on LB3PI1. Restoring it reinstates missing timestamps/Uncertain under the old
policy, so it should not be confused with a correction to Kepware.

At the end of automated validation, the final registered build was waiting for
PI Client activation. No visual PI result for the final build has yet been
claimed. Use the manual steps in PI_OPC_CLIENT_TEST.md and confirm new
REFRESH_CALLBACK / ASYNC_READ_CALLBACK / ADVISE_CALLBACK events for the actual
RDP server PID rather than reusing PID 9332's diagnostic events.

Subsequent RDP evidence at 13:08 UTC−3 on 2026-11-09 confirms PI Client visibly
showing TESTE2=1.000000, Good, VT_R8 and source timestamp 13:08:39. The normal
server PID was 2324; its read/Refresh callbacks returned S_OK. The first source
batch arrived 14.477 s after START, 1.326 s after the first read that displayed
0x800706BA. The process did not restart for recovery. DIGITAL and GUI-specific
Sync/Async/Advise remain separate observations pending confirmation. The generic
startup source-unavailable HRESULT can still produce a PI dialog before the
first sample; this startup presentation limitation remains recorded.
