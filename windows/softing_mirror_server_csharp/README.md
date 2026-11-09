# Softing C# OPC DA mirror (laboratory prototype)

This directory is an isolated alternative to `windows/softing_mirror_server` (C++). It is a **simulated-source** OPC DA server prototype; it does not yet connect to the machine 1 OPC UA endpoint. Nothing in the build automatically registers a COM server or installs a service.

## Item IDs and behavior

The prototype ProgID is `OPCBridge.Mirror.CSharp.1` (version independent: `OPCBridge.Mirror.CSharp`). The two read-only Item IDs are:

| Item ID | OPC DA value type | Simulated behavior |
|---|---|---|
| `OPCBridge.Mirror.CSharp.Lab.Numeric` | `VT_R8` / .NET `Double` | Varies around 21.0 once per second |
| `OPCBridge.Mirror.CSharp.Lab.Running` | `VT_BOOL` / .NET `Boolean` | Alternates every ten samples |

Browse the hierarchy `OPCBridge.Mirror.CSharp.Lab` to find both leaves. Both items advertise `READABLE`, not writable. A write transaction is explicitly completed with `OPC_E_BADRIGHTS` (`0xC0040006`); it does not update the cache. Values are latest-sample cache only. There is no event history, durable queue, or replay after interruption.

At startup, the item data types are known but no sample exists: quality is `Bad_WaitingForInitialData`, with no source timestamp. After a valid simulated sample, quality is DA `GOOD` (`0xC0`) and the timestamp is the sample's UTC timestamp. Press **P** to toggle source availability. On disconnect, the last value and its old timestamp remain visible with DA `BAD_COMM_FAILURE` (`0x18`) instead of Good; before any sample, quality is `BAD_NOT_CONNECTED` (`0x08`). Press **P** again to recover. Press **Q** to stop.

Missing, non-UTC, pre-FILETIME-epoch, or more than five seconds future timestamps are invalid. Invalid timestamps produce DA `BAD` (`0x00`) and are not exposed as a source timestamp. `ValueQT` requires a `DateTime`, so the adapter passes the explicit FILETIME epoch sentinel `1601-01-01T00:00:00Z` for absent timestamps while retaining Bad quality. It never replaces the timestamp with the machine clock. The simulator itself creates UTC timestamps; invalid cases are covered by the independent model tests.

The UTF-8 append log `OPCBridge.Mirror.CSharp.log` records startup, simulated connection transitions, errors and shutdown. No credentials are used by this simulator.

## Dependencies and architecture

The prototype targets **.NET Framework 4.6.1** and the pinned Softing OPC Classic SDK checkout at revision `08f4b3e968acfce4b775299741cef1819e24fda1`:

* Managed wrapper: `TBN461.dll` (built from the SDK's .NET source, AnyCPU).
* Native SDK runtime: `OTBu.dll` for x86 processes or `OTBux64.dll` for x64 processes. The .NET wrapper P/Invokes these names according to process architecture. Keep executable, wrapper/runtime deployment, and all dependent native binaries architecture-matched.
* Native SDK build: Visual Studio 2017 / MSVC v141 and the Windows SDK used by the SDK's `OTB_VS2017.vcxproj` (`ReleaseU|Win32` or `ReleaseU|x64`). A matching Microsoft Visual C++ 2017 runtime may be needed because the native project uses the dynamic CRT.
* The sample project targets .NET Framework 4.6.1. That framework release is retired ([Microsoft lifecycle](https://learn.microsoft.com/lifecycle/products/microsoft-net-framework)); the accessible Windows lab has .NET Framework 4.8, under which only the standalone model harness was run. The SDK separately documents a requirement for OPC Foundation OPC Core Components/redistributables; install and licensing are separate from the Softing source license.

Softing's checked-in `LICENSE` is MIT; retain its notices. That license covers the licensed repository contents, not third-party OPC Foundation components, Microsoft runtimes, or any separate commercial binary activation/license terms. Review the SDK's `NOTICE`/third-party notices and obtain any required runtime or feature license from the respective owners before redistribution or production use. This sample has no binary activation key configured. Softing identifies the Classic SDK repository as discontinued; it is suitable here for a bounded lab investigation, not a claim of current vendor support. See the [pinned SDK source](https://github.com/SoftingIndustrial/OPC-Classic-SDK/tree/08f4b3e968acfce4b775299741cef1819e24fda1) and its [license](https://github.com/SoftingIndustrial/OPC-Classic-SDK/blob/08f4b3e968acfce4b775299741cef1819e24fda1/LICENSE).

The source checkout contains the managed and native source projects, but this checkout did not contain built `TBN461.dll`, `OTBu.dll`, or `OTBux64.dll`. Therefore no full server binary can be built or launched from this Linux checkout alone. Build the native SDK and managed wrapper in a properly licensed Windows developer environment. x86 and x64 are project configurations, not evidence of successful builds: neither configuration has yet been built here. The existing ABB Automation COM interface was validated only in x86; this does not establish that the Softing mirror or ABB server works in x64.

**Windows XP is not supported or validated by this prototype.** The selected sample targets .NET Framework 4.6.1, while Microsoft's current framework support matrix excludes Windows XP; the pinned SDK's VS2017 native project and its Windows SDK/toolchain also do not provide evidence of XP support. Reconsidering XP requires a separate older runtime/SDK/toolchain and native dependency audit, then real tests. Do not infer XP compatibility from older SDK sample project files. See Microsoft's [.NET Framework system requirements](https://learn.microsoft.com/dotnet/framework/get-started/system-requirements).

## Build

On Windows with Visual Studio 2017 + C++ v141, Windows SDK, .NET Framework 4.6.1 targeting pack, the checked-out SDK, and its licensing prerequisites:

1. Build `development\core\src\OTB\OTB_VS2017.vcxproj` in `ReleaseU|Win32` and `ReleaseU|x64` to create the matching native DLL. Build the .NET wrapper project `development\NET\src\OPCToolboxNET_VS2017.csproj` as AnyCPU to produce `TBN461.dll`.
2. Copy only the matching native runtime and `TBN461.dll` beside the output executable. The project expects the wrapper at `development\NET\src\bin\VS2017\AnyCPU\Release\TBN461.dll`; pass `/p:SoftingSdkRoot=<SDK checkout path>` if the pinned checkout is elsewhere.
3. Build one architecture at a time:

```powershell
msbuild C:\src\OPC-Classic-SDK\development\core\src\OTB\OTB_VS2017.vcxproj /m /p:Configuration=ReleaseU /p:Platform=Win32
msbuild C:\src\OPC-Classic-SDK\development\NET\src\OPCToolboxNET_VS2017.csproj /m /p:Configuration=Release /p:Platform=AnyCPU
msbuild .\windows\softing_mirror_server_csharp\OPCBridge.Mirror.CSharp.csproj /m /p:Configuration=Release /p:Platform=x86 /p:SoftingSdkRoot=C:\src\OPC-Classic-SDK
Copy-Item C:\src\OPC-Classic-SDK\development\core\src\OTB\bin\VS2017\Win32\ReleaseU\OTBu.dll .\windows\softing_mirror_server_csharp\bin\x86\Release\
Copy-Item C:\src\OPC-Classic-SDK\development\NET\src\bin\VS2017\AnyCPU\Release\TBN461.dll .\windows\softing_mirror_server_csharp\bin\x86\Release\
```

For x64 use `/p:Platform=x64` and copy `development\core\src\OTB\bin\VS2017\x64\ReleaseU\OTBux64.dll` and the same `TBN461.dll` beside `bin\x64\Release\OPCBridge.Mirror.CSharp.exe`. Confirm outputs in that checkout before copying. Do not copy both architectures into an arbitrary production directory.

### Windows 7 SP1 x86 build gate

For a 32-bit Windows 7 target, build only the native `ReleaseU|Win32` configuration and the application as `Release|x86`; its native runtime is `OTBu.dll`. Windows 7 SP1 is a test target here, not a verified compatibility claim. Before building, confirm that the selected compiler/SDK can target Windows 7 and that the target has the required .NET Framework and VC++/Universal CRT runtimes. The .NET Framework 4.8 runtime alone does not prove the 4.6.1 targeting pack is installed.

The SDK's `OTB_VS2017.vcxproj` post-build commands copy `OTBu.dll`/`OTBux64.dll` into the SDK's `development\core\bin` directory and import libraries into `development\core\lib`. `OPCToolboxNET_VS2017.csproj` copies `TBN461.dll` into the SDK's `development\NET\bin` directory. These are file copies inside the SDK tree; neither project invokes `/RegServer` or installs a service. Build from an isolated staging copy of the pinned SDK so generated files do not land in the source checkout. The adapted C# application project has no post-build event. Do not build the upstream AddressSpace console/service project files: their post-build commands invoke `/RegServer`.

The x86 commands are the same three build commands above with `Platform=Win32` for the native project, `Platform=AnyCPU` for the wrapper, and `Platform=x86` for the adapted application. Full execution on Windows 7 still requires DA client testing; a successful compile alone does not validate runtime compatibility.

The adapted project has **no post-build event**. The upstream C# sample's VS2003–VS2017 console/service project files include `/RegServer` post-build commands; this project intentionally omits them. The only registration hook is an explicit `OPCBridge.Mirror.CSharp.exe /RegServer` argument, which delegates registration to the Softing toolkit and changes Windows COM registry entries. `/UnregServer` similarly removes them. Neither has been run. Do not run those commands except as a separately planned lab step with the correct architecture and administrator context; keep a registry backup/rollback procedure. No installutil/service setup is provided.

## Run and client validation (Windows lab)

Before running, place the architecture-matched native DLL next to the executable, confirm the .NET 4.6.1 runtime and OPC Core Components prerequisites, and use a disposable lab machine. The server uses classic COM local-server registration, so a DA client will not discover the ProgID until the explicit registration step has been separately approved and run. Registration is not performed by the build or normal startup.

After that separate registration step, launch the executable in an interactive console. It starts its simulator locally; do not expose this prototype to untrusted networks. A DA client procedure:

1. Connect to ProgID `OPCBridge.Mirror.CSharp.1` on the same host; browse the root and verify both full Item IDs above.
2. Read each item and verify `Double`/`VT_R8` and `Boolean`/`VT_BOOL`, initial waiting quality, then Good quality and recent UTC timestamps after a sample.
3. Subscribe to both items and observe periodic reports; values need not change on every report.
4. Attempt a write to each item. Expect `OPC_E_BADRIGHTS` (`0xC0040006`); read again and confirm the cache did not change.
5. Press P in the server console. Confirm reads/subscriptions become `BAD_COMM_FAILURE` while cached values and the last source timestamp remain old; after P again, confirm fresh Good samples. To inspect the startup state, connect/read before the first one-second simulator tick.
6. Press Q to stop and inspect the text log.

These steps have **not** been run with a DA client or PI Interface. PI integration, browse behavior across the target PI version, subscription semantics, timestamp/quality representation, and runtime registration remain to be validated on the actual machine 2. The later UA client must validate endpoint security/certificates and map UA `StatusCode`/`SourceTimestamp` into the model; that integration is intentionally out of scope here.

## Model-only tests

`tests\ModelTests.csproj` uses no third-party test framework. On Windows with the .NET 4.6.1 targeting pack run:

```powershell
msbuild .\windows\softing_mirror_server_csharp\tests\ModelTests.csproj /p:Configuration=Release
.\windows\softing_mirror_server_csharp\tests\bin\ModelTests.exe
```

It checks initial non-Good quality, sample value/type and source timestamp, invalid/missing timestamps, rejection of mismatched and non-finite numeric values, degraded quality on disconnect, cache retention without Good, and recovery. These tests do not load the Softing SDK, invoke COM, or prove browse/subscription behavior.

## Comparison with existing C++ prototype

The existing C++ implementation remains untouched and is still the better project-integrated path: it already consumes the project's snapshot/cache concepts and has portable tests. It has not been compiled or tested with PI. Its current invalid timestamp path substitutes the current time, so it needs a focused fix and tests before source timestamps can be trusted. This C# alternative follows Softing's AddressSpace sample and makes data-quality decisions in a testable managed model, but adds the older .NET wrapper, native architecture-specific SDK binaries, classic COM registration, and separate licensing/runtime prerequisites. Neither option is validated with PI; this C# prototype also has no machine 1 OPC UA source yet.
