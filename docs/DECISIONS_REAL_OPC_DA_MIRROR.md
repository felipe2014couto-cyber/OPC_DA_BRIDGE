# Architectural Decision Record: Real OPC DA Mirror Server

> **Stage 3 decision (supersedes the Stage 2 recommendation below):** use the official Softing OPC Classic SDK source at immutable commit `08f4b3e968acfce4b775299741cef1819e24fda1` under MIT, with a separate C++ Windows server process. The cache link is local JSON Lines over IPv4 loopback. See [THIRD_PARTY.md](THIRD_PARTY.md) and [the Windows server boundary](../windows/softing_mirror_server/README.md). The earlier Advosol recommendation is historical only and is no longer the selected SDK. No COM registration is authorized in this stage. The SDK sample server processes `-regserver`/`/regserver` via its `processCommandLine` path; **do not invoke this command**. Registration behavior and the CLSID/ProgID-specific key set depend on the server configuration and must be reviewed against the built binary before any later lab registration. If that later step is proposed, first export the precise affected keys and use the SDK's corresponding `-unregserver`/`/unregserver` path only after verifying its rollback behavior; test solely in a disposable lab VM. No registration was attempted here.

**Project**: OPC-DA-Bridge
**Component**: Windows OPC DA Mirror Server
**ProgID (Proposed Placeholder)**: `OPCBridge.Mirror.1` (Version-Independent: `OPCBridge.Mirror`)
**CLSID (Proposed Placeholder)**: `{B7A45C89-4D3B-4921-9E9A-8F5D890E71E2}`
**Date**: October 8, 2026 (Stage 2)
**Status**: ARCHITECTURAL EVALUATION & CONTRACT SPECIFICATION (Contratos/mocks e decisão; servidor COM real NÃO implementado nem validado no Windows/laboratório ou com PI Interface).

---

## 1. Executive Summary & Core Decision

Stage 2 of **OPC-DA-Bridge** evaluates available technical avenues to expose the in-memory **Bounded Latest-Value Cache** as a Windows OPC DA (Data Access) Server for industrial client consumption (such as the OSIsoft/AVEVA PI Interface for OPC DA).

### Summary of Decisions
1. **Primary Recommendation**: **Advosol DANSrv (.NET Server Toolkit)**.
   - Proposed architecture: Out-of-process COM `LocalServer32` executable hosting an in-process .NET assembly plugin (`INsPlugin`).
   - Advosol encapsulates unmanaged DCOM vtables, custom COM marshalling, apartment threading, connection points (`IConnectionPointContainer`), and OPC DA 2.05a / 3.0 interfaces.
   - The plugin logic delegates read queries to the platform-neutral `OpcDaReadFacade` and strictly rejects writes.
   - **Architectural Pending Item**: `AdvosolPluginContract` in Python is a conceptual sketch. In a production Windows environment, C# cannot directly invoke in-memory Python objects without a defined local IPC or hosting boundary (e.g. named pipes, local RPC/gRPC, or embedded Python.NET runtime). This integration boundary is documented as pending future design.
2. **Open-Source Secondary Alternative**: **Softing OPC Classic SDK (MIT Archive)**.
   - Formally discontinued commercially by Softing in 2020/2021 (support ended March 31, 2021), with source code published under the MIT license on GitHub (`SoftingIndustrial/OPC-Classic-SDK`).
   - Usable as a zero-cost backup if commercial purchase is disallowed, provided copyright notices are retained and the organization accepts absence of vendor warranty or active maintenance.
3. **Rejected Options**:
   - **In-house C++/ATL from scratch**: High implementation and maintenance burden; elevated concurrency and DCOM marshalling complexity.
   - **Python OpenOPC / pywin32 / OpcDa**: These libraries do not provide a turn-key, industrial-ready OPC DA server. While `pywin32` supports COM class registration and dual interfaces, generic Automation `IDispatch` wrappers cannot satisfy custom unmanaged COM vtables (`IOPCServer`, `IOPCSyncIO`) expected by industrial clients.

---

## 2. Comprehensive Evaluation of Evaluated Options

### 2.1 Option 1: Commercial SDK – Advosol DANSrv (.NET Server Toolkit)
- **Official Source**: [https://www.advosol.com/product/5/opc-da-net-server-toolkit](https://www.advosol.com/product/5/opc-da-net-server-toolkit) (Accessed October 2026).
- **Official EULA**: [https://www.advosol.com/topic/licenseagreement](https://www.advosol.com/topic/licenseagreement) (October 2017).
- **Architecture**: Two-tier model. A native Windows EXE hosts unmanaged COM/DCOM interfaces and invokes a .NET assembly (`INsPlugin`).
- **Role**: Dedicated **Server** toolkit.
- **Interfaces Supported**: OPC DA 2.05a and 3.0 (`IOPCServer`, `IOPCItemMgt`, `IOPCGroupStateMgt`, `IOPCSyncIO`, `IOPCAsyncIO2`, `IOPCItemIO`, `IOPCSyncIO2`, `IOPCAsyncIO3`, `IConnectionPointContainer`, `IOPCBrowse`).
- **Toolchain & Editions**: Targets .NET Framework 4.8.1 or .NET 6/8/10 depending on current 2026 vendor build. Standard Edition supports static namespaces; Professional Edition supports dynamic address spaces.
- **Bitness & Marshalling**:
  - Advosol provides both 32-bit (x86) and 64-bit (x64) server EXEs.
  - Because it runs as an out-of-process EXE server (`LocalServer32`), standard Windows 32-bit/64-bit DCOM proxy/stub marshalling is used.
  - **Required Laboratory Verification**: Sources confirm DA 2.05a/3.0 support and x86/x64 EXEs, but do NOT constitute formal certification with OSIsoft/AVEVA PI Interface. Testing must explicitly validate PI Interface x86 -> Mirror x86 (baseline) and PI Interface x86 -> Mirror x64 (additional test) with standard OPC Core Components proxy/stubs.
- **License & Costs**:
  - Evaluation: $0 USD (runtime limited to 30 minutes; redistribution prohibited).
  - Standard Edition (Single Developer): $1,250 USD.
  - Professional Edition (Single Developer): $2,090 USD.
  - Standard Site License: $3,190 USD; Professional Site License: $5,490 USD.
  - Redistribution: Royalty-free binary redistribution of server runtime components bundled with application. Vendor SDK source/documentation redistribution prohibited; confirm the terms supplied with the purchased version.
- **Risk Assessment**: Lowest estimated architectural risk among commercial options, subject to licensing approval and verification of local IPC/hosting boundary to Python.

### 2.2 Option 2: Discontinued Commercial SDK – Softing Classic OPC Toolkit (dataFEED Classic)
- **Official EOL Notice**: [https://industrial.softing.com/us/services/discontinued-products.html](https://industrial.softing.com/us/services/discontinued-products.html) (Accessed October 2026).
- **Official Open-Source Repository**: [https://github.com/SoftingIndustrial/OPC-Classic-SDK](https://github.com/SoftingIndustrial/OPC-Classic-SDK) (Accessed October 2026).
- **Status**: **End-of-Life (EOL)**. Commercial sales ended April 1, 2020; support ended March 31, 2021.
- **Open-Source MIT Release**: Published under MIT license on GitHub (`SoftingIndustrial/OPC-Classic-SDK`).
  - Covers DA 1.0, 2.05a, and 3.0 in C++, C#, and VB.NET.
  - Redistribution requires retention of copyright notice and permission notice.
- **Toolchain & Build Constraints**:
  - Built historically with legacy Visual Studio / MSVC toolsets.
  - Build scripts and x86/x64 configurations have not been validated against modern 2026 toolchains and require engineering effort to verify compatibility.
- **Risk Assessment**: Moderate-to-high lifecycle risk. No license fee for the published MIT source; no vendor warranty. Commercial support ended, and maintenance of the adopted build is an internal responsibility.

### 2.3 Option 3: OPC Foundation Classic Samples & Reference Server
- **Official Source**: [https://opcfoundation.org/developer-tools/samples-and-tools-classic/net-api-sample-client-source-code/](https://opcfoundation.org/developer-tools/samples-and-tools-classic/net-api-sample-client-source-code/) (Accessed October 2026).
- **License & Terms**: The specific .NET sample client and reference server page declares code under the **MIT License**, available to Corporate Members, with an announced removal date of September 1, 2026.
- **Clarification**: Classic samples under MIT must not be confused with the OPC UA Reference Commercial License (RCL). Generic redistribution of MIT-licensed samples is permissible with notice, but download access requires active membership credentials.
- **Scope & Limitations**:
  - x86 vs. x64 availability and DA 2.05a vs. DA 3.0 completeness depend on the specific downloaded sample package.
  - Code was written as educational reference implementations rather than hardened production server runtimes.

### 2.4 Option 4: Custom In-House C++/ATL Development
- **Architecture**: Custom out-of-process COM server using ATL/C++.
- **Toolchain & Cost**: Visual Studio Professional/Enterprise licensing cost depends on organization size and licensing qualifications (see [Microsoft Visual Studio Licensing Guidance](https://www.microsoft.com/licensing/guidance/Visual-Studio) and [Visual Studio Pricing](https://visualstudio.microsoft.com/vs/pricing/?tab=free-tools)).
- **Technical Complexity**: High engineering effort required to implement custom COM vtables, multithreaded apartment synchronization, connection points, and security descriptors without off-the-shelf framework support.

### 2.5 Option 5: Python-Based Libraries (OpenOPC, pywin32, OpcDa)
- **License Specifics**:
  - `pywin32`: README at [https://github.com/mhammond/pywin32#licenses](https://github.com/mhammond/pywin32#licenses) states that pywin32 mixes licenses (some PSF, some author-specific) and license files/notices are the authoritative source of truth.
  - `OpenOPC`: Typically GPL (OpenOPC for Python 2.x/3.x forks) or author-specific license; primarily an Automation client wrapper.
  - `OpcDa`: Author metadata at [https://pypi.org/project/OpcDa/](https://pypi.org/project/OpcDa/) declares MIT; notices must be verified prior to use.
- **Technical Limitations**: None of these packages provide a proven, turn-key OPC DA server implementation. While `pywin32` allows creating COM servers, generic `IDispatch` Automation servers cannot satisfy unmanaged custom COM vtables (`IOPCServer`, `IOPCSyncIO`) demanded by industrial DA clients like PI Interface.

---

## 3. PI Interface Compatibility & Bitness Constraints

- **Observed PI Interface Version**: Catalog inspection reveals OSIsoft PI Interface for OPC DA version `2.7.2.20`.
- **Bitness**: The cataloged v2.7.2.20 is a 32-bit (x86) application; no local executable was inspected. (Note: other interface versions or site installations may differ; this assessment applies to the cataloged v2.7.2.20).
- **Registration-Free COM Limitations**: The Microsoft ClickOnce mechanism for registration-free COM does not cover out-of-process EXE servers. No supported registration-free activation of DANSrv by PI Interface was established in this stage; future activation must follow the SDK documentation and approved laboratory registration plan.
- **Timestamping Configuration**: Clients reading mirrored tags must consume the server-provided OPC timestamp. Tag-level configuration details are outside bridge scope.

---

## 4. Planned Rollback & Registry Plan (NOT Executed)

> [!CAUTION]
> NO COM REGISTRATION HAS BEEN EXECUTED DURING THIS EVALUATION.
> ProgID `OPCBridge.Mirror.1` and CLSID `{B7A45C89-4D3B-4921-9E9A-8F5D890E71E2}` are proposed placeholders and were not registered by this task. Preexisting entries were not inspected.

When future laboratory registration is explicitly authorized:
1. **Pre-Registration Inventory**:
   - Inspect both 32-bit and 64-bit views (`HKCR\CLSID`, `HKCR\Wow6432Node\CLSID`, `HKCR\OPCBridge.Mirror`, `HKCR\OPCBridge.Mirror.1`, `HKCR\AppID`, `HKCR\TypeLib`, `HKCR\Component Categories`).
   - Confirm no collisions exist with existing industrial software.
2. **Planned Rollback Strategy**:
   - If previous entries existed, export keys to restore previous state rather than deleting shared branches.
   - Remove only newly created keys:
     - `HKCR\CLSID\{B7A45C89-4D3B-4921-9E9A-8F5D890E71E2}`
     - `HKCR\Wow6432Node\CLSID\{B7A45C89-4D3B-4921-9E9A-8F5D890E71E2}`
     - `HKCR\OPCBridge.Mirror.1`
     - `HKCR\OPCBridge.Mirror` (VersionIndependentProgID)
     - Associated AppID entries and Category implementations.

## Consolidated evidence

See [STAGE2_HANDOVER.md](STAGE2_HANDOVER.md) for the complete comparison matrix, official sources for C++/ATL/Python/PI, exact initial Git state, final validation and laboratory registration/rollback templates.
