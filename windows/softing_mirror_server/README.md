# OPC DA Mirror server (initial implementation)

This is a separate, read-only Windows process. It exposes the Python bridge's latest snapshots as OPC DA VQT values. It does not import Python, query an upstream OPC server, or perform writes.

## Namespace and bridge

COM and browse identity:

| Field | Value |
|---|---|
| ProgID | `OPCBridge.Mirror.1` |
| Version independent ProgID | `OPCBridge.Mirror` |
| Display name | `OPCBridge Mirror` |
| Browse namespace root | `OPCBridge.Mirror` |
| CLSID | `{B7A45C89-4D3B-4921-9E9A-8F5D890E71E2}` |

The CLSID reuses the GUID already proposed in `docs/DECISIONS_REAL_OPC_DA_MIRROR.md`; it was not regenerated. Treat it as the permanent class identity across builds and versioned ProgIDs. Its availability in the Windows registry has not been checked; check for collisions before any separately authorized registration.

The initial static namespace is:

```text
OPCBridge
  Mirror
    Lab
      Temperature  -> Simulated.Furnace.Temperature
      Pressure     -> Simulated.Line.Pressure
```

The tag item IDs are `OPCBridge.Mirror.Lab.Temperature` and `OPCBridge.Mirror.Lab.Pressure`.

The process polls the bridge on IPv4 loopback `127.0.0.1:45871` using JSON Lines protocol v1 (`{"op":"snapshot","items":[...]}`). A background worker performs socket I/O; Softing DA transaction callbacks only copy the latest in-memory snapshot. Sequence regressions are discarded. A disconnected, stale, or non-Good bridge record is never raised to Good. OPC Write/WriteVQT requests return `E_ACCESSDENIED`, and the tags are advertised read-only. The `Browse` implementation hook is the SDK address-space root's `queryAddressSpaceElementChildren`; initial children are constructed statically, and future dynamic browse belongs there.

`SnapshotStore` contains the platform-neutral sequence, freshness, and quality rules. The Windows adapter is intentionally thin and keeps the callback and browse extension points explicit.

## SDK dependency

- Official upstream: `https://github.com/SoftingIndustrial/OPC-Classic-SDK`
- Exact pinned commit: `08f4b3e968acfce4b775299741cef1819e24fda1`
- Local reference checkout: `../../.vendor/softing-opc-classic-sdk` (ignored by Git; do not copy it into this directory or commit it).
- The SDK is discontinued and published under the upstream MIT license; preserve its license and notices when redistributing SDK source or binaries.
- The pinned checkout contains the projects and source that generate the import libraries; no prebuilt Softing binaries are present. `development/OPCToolbox_VS2017.sln` declares the C++ API project dependent on the core project. For the requested names, x86 `Release|Win32` builds `OTB.lib` first and then `TBCVS2017.lib`; `Debug|Win32` builds `OTBD.lib` first and then `TBCVS2017D.lib`. The core project is `development/core/src/OTB/OTB_VS2017.vcxproj`; the API project is `development/C++/src/OPCToolboxC++_VS2017.vcxproj`. Both projects' post-build steps copy their import libraries into `development/core/lib` and `development/C++/lib`, respectively. These libraries are generated from the pinned checkout and do not require a separate Softing binary package; the SDK source itself is present here.

This project is built as Unicode, so it links the SDK's matching `ReleaseU|Win32`/`DebugU|Win32` variants: `TBCVS2017u.lib`/`TBCVS2017uD.lib`. In both Unicode configurations, the C++ API project links `OTBu.lib`. The `TBCVS2017.lib`/`TBCVS2017D.lib` names above are the SDK's MultiByte configurations (with `OTB.lib`/`OTBD.lib`) and must not be mixed with this project's Unicode build.

The SDK's `development/C++/samples/server/AddressSpace` supplies the API pattern used here (`MyCreator.h`, `MyDaAddressSpaceRoot.h`, `MyTransaction.h`, `OpcServer.cpp`, `Console.cpp`). Its VS2017 project copies the matching API and core DLLs beside the sample executable. For this Unicode x86 Release build, transfer these files together: `OPCBridge.Mirror.1.exe`, `TBCVS2017u.dll`, and `OTBu.dll`. A Unicode Debug executable uses `TBCVS2017uD.dll` and `OTBu.dll`, as shown by the sample project. The SDK requires the x86 OPC Core Components on each machine running a DA server; those are a separate system prerequisite, not side-by-side DLLs. Because this project's MSVC runtime is dynamic, the matching Visual C++ 2017 x86 runtime must also be present on B. The sample's Visual Studio project has a post-build `/RegServer` command; this project deliberately has no registration build step.

## Build on Windows 7 x86

Use Visual Studio 2017 C++ Build Tools (v141), MSBuild, and the Windows 8.1 SDK targeting Windows 7. Build the Softing x86 `ReleaseU|Win32` libraries in the order above, then open `OPCBridge.Mirror.sln`, choose `Release|Win32`, and set `SoftingSdkLibDir` to the generated `development/C++/lib` directory. The project builds `OPCBridge.Mirror.1.exe` as x86. The project doesn't set up COM registration, DCOM, a service, firewall rules, or SDK licensing; those are separate deployment decisions requiring separate review.

Example build commands from `windows\softing_mirror_server` (first generate the SDK libraries in the stated order):

```bat
msbuild ..\..\.vendor\softing-opc-classic-sdk\development\core\src\OTB\OTB_VS2017.vcxproj /p:Configuration=ReleaseU /p:Platform=Win32
msbuild ..\..\.vendor\softing-opc-classic-sdk\development\C++\src\OPCToolboxC++_VS2017.vcxproj /p:Configuration=ReleaseU /p:Platform=Win32
msbuild OPCBridge.Mirror.sln /p:Configuration=Release /p:Platform=Win32
```

## Local tests

The platform-neutral C++ snapshot model has a small host-compiler test, invoked by pytest when `g++` or `clang++` is available. It does not load COM or the Softing SDK. The Windows executable itself must be compiled on a Windows toolchain with the matching SDK library; this Linux workspace cannot validate that final link.

COM registration, DCOM, registry, firewall, service installation, remote hosts, PI, and upstream OPC remain outside this implementation stage.
