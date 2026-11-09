#!/usr/bin/env python3
"""Prepare a temporary pinned Rust OPC workspace with DA browse diagnostics.

The upstream checkout is copied to the build directory and is never edited.
This is an internal lab build patch; it does not vendor the generated OPC
bindings into this repository.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path


def replace_once(path: Path, old: str, new: str) -> None:
    source = path.read_text()
    count = source.count(old)
    if count != 1:
        raise SystemExit(f"expected one patch point in {path}, found {count}: {old!r}")
    path.write_text(source.replace(old, new))


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: patch_rust_opc_da_browse.py UPSTREAM_CHECKOUT DESTINATION")
    upstream, destination = map(Path, sys.argv[1:])
    if destination.exists():
        raise SystemExit(f"refusing to overwrite existing temporary workspace: {destination}")

    destination.mkdir(parents=True)
    root_manifest = (upstream / "Cargo.toml").read_text()
    old_members = '''members = [
  "opc_ae_bindings",
  "opc_classic_abi",
  "opc_classic_types",
  "opc_classic_utils",
  "opc_comn_bindings",
  "opc_da_bindings",
  "opc_hda_bindings",
]'''
    new_members = '''members = [
  "opc_classic_abi",
  "opc_classic_types",
  "opc_classic_utils",
  "opc_comn_bindings",
  "opc_da_bindings",
]'''
    if root_manifest.count(old_members) != 1:
        raise SystemExit("could not isolate the required upstream workspace members")
    (destination / "Cargo.toml").write_text(root_manifest.replace(old_members, new_members))

    packages = (
        "opc_classic_abi",
        "opc_classic_types",
        "opc_classic_utils",
        "opc_comn_bindings",
        "opc_da_bindings",
    )
    for package in packages:
        shutil.copytree(upstream / package, destination / package)

    server = destination / "opc_da_bindings/src/server/mod.rs"
    replace_once(
        server,
        "use windows::Win32::System::Com::{CoTaskMemAlloc, CoTaskMemFree};",
        "use windows::Win32::System::Com::{CoTaskMemAlloc, CoTaskMemFree, IEnumString, IEnumString_Impl, IConnectionPointContainer};",
    )
    replace_once(
        server,
        "    IOPCServer_Impl, IOPCSyncIO, IOPCSyncIO_Impl, tagOPCDATASOURCE, tagOPCENUMSCOPE, tagOPCITEMDEF,\n",
        "    IOPCServer_Impl, IOPCSyncIO, IOPCSyncIO_Impl, IOPCBrowseServerAddressSpace, IOPCAsyncIO2,\n"
        "    IOPCBrowseServerAddressSpace_Impl, tagOPCDATASOURCE, tagOPCENUMSCOPE, tagOPCITEMDEF,\n"
        "    tagOPCBROWSEDIRECTION, tagOPCBROWSETYPE, tagOPCNAMESPACETYPE, OPC_BRANCH, OPC_FLAT,\n"
        "    OPC_LEAF, OPC_NS_FLAT, OPC_NS_HIERARCHIAL,\n",
    )
    replace_once(
        server,
        "use windows::Win32::Foundation::{E_INVALIDARG, E_NOTIMPL, E_OUTOFMEMORY, E_POINTER, E_UNEXPECTED};",
        "use windows::Win32::Foundation::{E_FAIL, E_INVALIDARG, E_NOTIMPL, E_OUTOFMEMORY, E_POINTER, E_UNEXPECTED};",
    )
    replace_once(
        server,
        "#[derive(Clone, Debug)]\npub struct ServerItemResult<T> {",
        "#[derive(Clone, Debug, Eq, PartialEq)]\npub struct BrowseItem {\n"
        "    pub item_id: String,\n    pub data_type: u16,\n    pub access_rights: u32,\n}\n\n"
        "#[derive(Clone, Debug)]\npub struct ServerItemResult<T> {",
    )
    replace_once(
        server,
        "pub trait DaService: Send + Sync + 'static {\n    fn status(&self) -> Result<ServerStatus>;",
        "pub trait DaService: Send + Sync + 'static {\n"
        "    fn browse_items(&self) -> Vec<BrowseItem> { Vec::new() }\n"
        "    fn browse_hierarchical(&self) -> bool { false }\n"
        "    fn status(&self) -> Result<ServerStatus>;",
    )
    replace_once(
        server,
        "#[windows_core::implement(IOPCServer, AbiCommon)]",
        "#[windows_core::implement(IOPCServer, AbiCommon, IOPCBrowseServerAddressSpace, IConnectionPointContainer)]",
    )
    replace_once(
        server,
        "    next_group_handle: AtomicU32,\n}",
        "    next_group_handle: AtomicU32,\n    shutdown: Arc<lab_refresh::ShutdownState>,\n"
        "    browse_position: Mutex<lab_browse::Position>,\n}",
    )
    replace_once(
        server,
        "            next_group_handle: AtomicU32::new(1),\n        }",
        "            next_group_handle: AtomicU32::new(1),\n            shutdown: Arc::new(lab_refresh::ShutdownState::new()),\n"
        "            browse_position: Mutex::new(lab_browse::Position::default()),\n        }",
    )
    replace_once(
        server,
        "pub trait DaGroupService: Send + Sync + 'static {\n    fn add_items(&self, items: &[ItemSpec]) -> Result<Vec<ServerItemResult<ServerAddedItem>>>;",
        "pub trait DaGroupService: Send + Sync + 'static {\n"
        "    fn diagnostic(&self, _event: &str) {}\n"
        "    fn active_refresh_items(&self) -> Result<Vec<(ServerItemHandle, ClientItemHandle)>> { Ok(Vec::new()) }\n"
        "    fn validate_read_handles(&self, handles: &[ServerItemHandle]) -> Result<Vec<ServerItemResult<ClientItemHandle>>> {\n"
        "        Ok(self.read(DataSource::Cache, handles)?.into_iter().map(|r| ServerItemResult { result: r.result.map(|s| s.client_handle) }).collect())\n    }\n"
        "    fn add_items(&self, items: &[ItemSpec]) -> Result<Vec<ServerItemResult<ServerAddedItem>>>;",
    )
    replace_once(
        server,
        "#[windows_core::implement(IOPCItemMgt, IOPCSyncIO, IOPCGroupStateMgt)]\nstruct DaGroupAdapter {\n    service: Arc<dyn DaGroupService>,\n    state: Mutex<GroupState>,",
        "#[windows_core::implement(IOPCItemMgt, IOPCSyncIO, IOPCGroupStateMgt, IOPCAsyncIO2, IConnectionPointContainer)]\n"
        "struct DaGroupAdapter {\n    service: Arc<dyn DaGroupService>,\n    state: Arc<Mutex<GroupState>>,\n"
        "    refresh: Arc<lab_refresh::RefreshState>,",
    )
    replace_once(
        server,
        "            service,\n            state: Mutex::new(state),\n        }\n    }\n}\n\n#[allow(clippy::not_unsafe_ptr_arg_deref)]\nimpl IOPCItemMgt_Impl",
        "            service,\n            state: Arc::new(Mutex::new(state)),\n"
        "            refresh: Arc::new(lab_refresh::RefreshState::new()),\n"
        "        }\n    }\n}\n\n#[allow(clippy::not_unsafe_ptr_arg_deref)]\nimpl IOPCItemMgt_Impl",
    )
    with server.open("a") as target:
        target.write("\nmod lab_refresh;\nmod lab_browse;\n")
    shutil.copyfile(
        Path(__file__).resolve().parent.parent / "windows/rust_da_com_lab/src/browse_tree.rs",
        server.parent / "lab_browse.rs",
    )
    shutil.copyfile(
        Path(__file__).resolve().parent.parent / "windows/rust_da_com_lab/src/refresh_adapter.rs",
        server.parent / "lab_refresh.rs",
    )
    replace_once(
        server,
        """    ) -> AbiResult<()> {
        serve(|| {
            unsafe {
                initialize_output(values)?;
                initialize_output(errors)?;""",
        """    ) -> AbiResult<()> {
        self.service.diagnostic(&format!(
            "IOPCSyncIO::Read ENTER source={} count={} handles_null={} values_null={} errors_null={}",
            source.0, count, handles.is_null(), values.is_null(), errors.is_null()
        ));
        let result = serve(|| {
            unsafe {
                initialize_output(values)?;
                initialize_output(errors)?;""",
    )
    replace_once(
        server,
        """            batch_status(has_error)
        })
    }

    fn Write(
""",
        """            batch_status(has_error)
        });
        let outcome = match &result {
            Ok(()) => "S_OK".to_string(),
            Err(error) => format!("0x{:08x}", error.code().0 as u32),
        };
        self.service.diagnostic(&format!("IOPCSyncIO::Read RETURN hresult={}", outcome));
        result
    }

    fn Write(
""",
    )

    browse_impl = r'''
#[allow(clippy::not_unsafe_ptr_arg_deref)]
impl IOPCBrowseServerAddressSpace_Impl for DaServerAdapter_Impl {
    fn QueryOrganization(&self) -> AbiResult<tagOPCNAMESPACETYPE> {
        if self.service.browse_hierarchical() {
            log_enum("BROWSE_QUERY_ORGANIZATION", "returning hierarchical namespace");
            return Ok(OPC_NS_HIERARCHIAL);
        }
        log_enum("BROWSE_QUERY_ORGANIZATION", "returning flat namespace");
        Ok(OPC_NS_FLAT)
    }

    fn ChangeBrowsePosition(
        &self,
        direction: tagOPCBROWSEDIRECTION,
        destination: &PCWSTR,
    ) -> AbiResult<()> {
        let destination = read_browse_string(destination)?;
        if self.service.browse_hierarchical() {
            let entries = hierarchical_entries(self.service.as_ref());
            let mut position = self.browse_position.lock().map_err(|_| AbiError::from_hresult(E_UNEXPECTED))?;
            log_enum("BROWSE_CHANGE_POSITION_REQUEST", &format!("direction={} destination={:?} current_position={:?}", direction.0, destination, position.path()));
            let result = position.change(direction.0, &destination, &entries).map_err(browse_tree_error);
            log_enum("BROWSE_CHANGE_POSITION_RESULT", &format!("hresult={:#010x} position={:?}", result.as_ref().err().map_or(0, |e| e.code().0 as u32), position.path()));
            return result;
        }
        log_enum(
            "BROWSE_CHANGE_POSITION_REQUEST",
            &format!(
                "direction={} destination={:?} current_position=flat-root",
                direction.0, destination
            ),
        );
        if !(1..=3).contains(&direction.0) {
            log_enum(
                "BROWSE_CHANGE_POSITION_RESULT",
                &format!("hresult=0x{:08x} position_unchanged=flat-root", E_INVALIDARG.0 as u32),
            );
            return Err(AbiError::from_hresult(E_INVALIDARG));
        }
        // The specification says ChangeBrowsePosition fails for FLAT spaces.
        // Returning S_OK here makes clients assume that UP changed state and
        // can trap flat-browser clients in an endless retry loop.
        log_enum(
            "BROWSE_CHANGE_POSITION_RESULT",
            &format!("hresult=0x{:08x} position_unchanged=flat-root", E_FAIL.0 as u32),
        );
        Err(AbiError::from_hresult(E_FAIL))
    }

    fn BrowseOPCItemIDs(
        &self,
        browse_type: tagOPCBROWSETYPE,
        filter: &PCWSTR,
        data_type_filter: u16,
        access_rights_filter: u32,
    ) -> AbiResult<IEnumString> {
        let filter = read_browse_string(filter)?;
        log_enum("BROWSE_ENUM_REQUEST", &format!("type={} filter={:?} vt_filter={} rights_filter={:#x}", browse_type.0, filter, data_type_filter, access_rights_filter));
        if self.service.browse_hierarchical() {
            let entries = hierarchical_entries(self.service.as_ref());
            let position = self.browse_position.lock().map_err(|_| AbiError::from_hresult(E_UNEXPECTED))?;
            let names = position.enumerate(browse_type.0, &filter, data_type_filter, access_rights_filter, &entries).map_err(browse_tree_error)?;
            log_enum("BROWSE_ENUM_RESULT", &format!("position={:?} item_count={} items={:?}", position.path(), names.len(), names));
            return Ok(EnumStringAdapter::new(names).into());
        }
        let mut names = Vec::new();
        match browse_type.0 {
            value if value == OPC_BRANCH.0 => {}
            value if value == OPC_LEAF.0 || value == OPC_FLAT.0 => {
                for item in self.service.browse_items() {
                    if data_type_filter != 0 && item.data_type != data_type_filter {
                        continue;
                    }
                    if access_rights_filter != 0
                        && item.access_rights & access_rights_filter != access_rights_filter
                    {
                        continue;
                    }
                    if !browse_pattern_matches(&filter, &item.item_id) {
                        continue;
                    }
                    names.push(item.item_id);
                }
            }
            _ => return Err(AbiError::from_hresult(E_INVALIDARG)),
        }
        names.sort();
        log_enum("BROWSE_ENUM_RESULT", &format!("item_count={} items={:?}", names.len(), names));
        Ok(EnumStringAdapter::new(names).into())
    }

    fn GetItemID(&self, item_data_id: &PCWSTR) -> AbiResult<PWSTR> {
        let item_data_id = read_browse_string(item_data_id)?;
        log_enum("BROWSE_GET_ITEM_ID", &format!("item_data_id={:?}", item_data_id));
        if self.service.browse_hierarchical() {
            let entries = hierarchical_entries(self.service.as_ref());
            let position = self.browse_position.lock().map_err(|_| AbiError::from_hresult(E_UNEXPECTED))?;
            let resolved = position.resolve(&item_data_id, &entries).map_err(browse_tree_error)?;
            log_enum("BROWSE_GET_ITEM_ID_RESULT", &format!("position={:?} resolved={:?}", position.path(), resolved));
            return Ok(PWSTR(OwnedPwstr::new(resolved).map_err(to_abi_error)?.into_raw()));
        }
        let exists = self
            .service
            .browse_items()
            .iter()
            .any(|item| item.item_id == item_data_id);
        if !exists {
            return Err(AbiError::from_hresult(E_INVALIDARG));
        }
        Ok(PWSTR(OwnedPwstr::new(item_data_id).map_err(to_abi_error)?.into_raw()))
    }

    fn BrowseAccessPaths(&self, item_id: &PCWSTR) -> AbiResult<IEnumString> {
        let item_id = read_browse_string(item_id)?;
        log_enum("BROWSE_ACCESS_PATHS", &format!("item_id={:?}", item_id));
        if !self
            .service
            .browse_items()
            .iter()
            .any(|item| item.item_id == item_id)
        {
            return Err(AbiError::from_hresult(E_INVALIDARG));
        }
        Ok(EnumStringAdapter::new(Vec::new()).into())
    }
}

fn hierarchical_entries(service: &dyn DaService) -> Vec<lab_browse::Entry> {
    service.browse_items().into_iter().map(|entry| lab_browse::Entry {
        item_id: entry.item_id, data_type: entry.data_type, access_rights: entry.access_rights,
    }).collect()
}

fn browse_tree_error(error: lab_browse::BrowseError) -> AbiError {
    AbiError::from_hresult(match error {
        lab_browse::BrowseError::AboveRoot => E_FAIL,
        lab_browse::BrowseError::InvalidArgument => E_INVALIDARG,
    })
}

fn read_browse_string(value: &PCWSTR) -> AbiResult<String> {
    if value.is_null() {
        return Ok(String::new());
    }
    unsafe { value.to_string() }.map_err(|_| AbiError::from_hresult(E_INVALIDARG))
}

fn browse_pattern_matches(pattern: &str, value: &str) -> bool {
    if pattern.is_empty() || pattern == "*" {
        return true;
    }
    let pattern: Vec<char> = pattern.chars().map(|c| c.to_ascii_lowercase()).collect();
    let value: Vec<char> = value.chars().map(|c| c.to_ascii_lowercase()).collect();
    let mut previous = vec![false; value.len() + 1];
    previous[0] = true;
    for token in pattern {
        let mut current = vec![false; value.len() + 1];
        if token == '*' {
            current[0] = previous[0];
            for index in 1..=value.len() {
                current[index] = previous[index] || current[index - 1];
            }
        } else {
            for index in 1..=value.len() {
                current[index] = previous[index - 1] && (token == '?' || token == value[index - 1]);
            }
        }
        previous = current;
    }
    previous[value.len()]
}

#[windows_core::implement(IEnumString)]
struct EnumStringAdapter {
    values: Vec<String>,
    position: Mutex<usize>,
}

impl EnumStringAdapter {
    fn new(values: Vec<String>) -> Self {
        Self { values, position: Mutex::new(0) }
    }
}

impl IEnumString_Impl for EnumStringAdapter_Impl {
    fn Next(&self, count: u32, output: *mut PWSTR, fetched: *mut u32) -> HRESULT {
        log_enum("ENUM_NEXT", &format!("count={} output_null={} fetched_null={}", count, output.is_null(), fetched.is_null()));
        if count != 0 && output.is_null() {
            return E_POINTER;
        }
        if count != 1 && fetched.is_null() {
            return E_POINTER;
        }
        if !fetched.is_null() {
            unsafe { fetched.write(0) };
        }
        if count == 0 {
            return HRESULT(0);
        }
        for index in 0..count as usize {
            unsafe { output.add(index).write(PWSTR::null()) };
        }
        let mut position = match self.position.lock() {
            Ok(position) => position,
            Err(_) => return E_UNEXPECTED,
        };
        let available = self.values.len().saturating_sub(*position);
        let returned = available.min(count as usize);
        log_enum("ENUM_NEXT_RESULT", &format!("returning={} new_position={}", returned, *position + returned));
        for offset in 0..returned {
            let encoded: Vec<u16> = self.values[*position + offset]
                .encode_utf16()
                .chain(std::iter::once(0))
                .collect();
            let bytes = match encoded.len().checked_mul(std::mem::size_of::<u16>()) {
                Some(bytes) => bytes,
                None => {
                    unsafe { free_enum_outputs(output, offset) };
                    return E_OUTOFMEMORY;
                }
            };
            let allocation = unsafe { CoTaskMemAlloc(bytes) }.cast::<u16>();
            if allocation.is_null() {
                unsafe { free_enum_outputs(output, offset) };
                return E_OUTOFMEMORY;
            }
            unsafe {
                std::ptr::copy_nonoverlapping(encoded.as_ptr(), allocation, encoded.len());
                output.add(offset).write(PWSTR(allocation));
            }
        }
        *position += returned;
        if !fetched.is_null() {
            unsafe { fetched.write(returned as u32) };
        }
        if returned == count as usize { HRESULT(0) } else { HRESULT(1) }
    }

    fn Skip(&self, count: u32) -> HRESULT {
        log_enum("ENUM_SKIP", &format!("count={}", count));
        let mut position = match self.position.lock() {
            Ok(position) => position,
            Err(_) => return E_UNEXPECTED,
        };
        let available = self.values.len().saturating_sub(*position);
        if count as usize <= available {
            *position += count as usize;
            HRESULT(0)
        } else {
            *position = self.values.len();
            HRESULT(1)
        }
    }

    fn Reset(&self) -> AbiResult<()> {
        log_enum("ENUM_RESET", "called");
        *self.position.lock().map_err(|_| AbiError::from_hresult(E_UNEXPECTED))? = 0;
        Ok(())
    }

    fn Clone(&self) -> AbiResult<IEnumString> {
        log_enum("ENUM_CLONE", "called");
        let position = *self.position.lock().map_err(|_| AbiError::from_hresult(E_UNEXPECTED))?;
        let clone: IEnumString = EnumStringAdapter {
            values: self.values.clone(),
            position: Mutex::new(position),
        }
        .into();
        Ok(clone)
    }
}

fn log_enum(kind: &str, message: &str) {
    use std::io::Write as _;
    let path = std::env::current_exe()
        .ok()
        .and_then(|executable| executable.parent().map(|parent| parent.join("rust-da-com-lab.log")))
        .unwrap_or_else(|| std::path::PathBuf::from("rust-da-com-lab.log"));
    if let Ok(mut file) = std::fs::OpenOptions::new().create(true).append(true).open(path) {
        let _ = writeln!(file, "{:?} pid={} [{}] {}", std::time::SystemTime::now(), std::process::id(), kind, message);
    }
}

unsafe fn free_enum_outputs(output: *mut PWSTR, count: usize) {
    for index in 0..count {
        let value = unsafe { output.add(index).read() };
        if !value.is_null() {
            unsafe { CoTaskMemFree(Some(value.0.cast())) };
            unsafe { output.add(index).write(PWSTR::null()) };
        }
    }
}
'''
    replace_once(
        server,
        "#[allow(clippy::not_unsafe_ptr_arg_deref)]\nimpl IOPCServer_Impl for DaServerAdapter_Impl {",
        browse_impl + "\n#[allow(clippy::not_unsafe_ptr_arg_deref)]\nimpl IOPCServer_Impl for DaServerAdapter_Impl {",
    )

    crate_root = destination / "opc_da_bindings/src/lib.rs"
    replace_once(
        crate_root,
        "// Generated Windows ABI symbols are private implementation details.\n",
        "// The laboratory browse adapter exposes this COM interface for its\n"
        "// self-test; it still uses the pinned generated ABI definitions.\n"
        "pub use bindings::{IOPCGroupStateMgt, IOPCAsyncIO2, IOPCDataCallback, IOPCDataCallback_Impl, OPC_DS_CACHE, OPC_DS_DEVICE};\n"
        "pub use bindings::IOPCBrowseServerAddressSpace as DaBrowseServerAddressSpace;\n"
        "pub use bindings::tagOPCBROWSETYPE as DaBrowseType;\n"
        "pub use bindings::tagOPCBROWSEDIRECTION as DaBrowseDirection;\n"
        "pub use bindings::OPC_LEAF as DA_BROWSE_LEAF;\n"
        "pub use bindings::OPC_BRANCH as DA_BROWSE_BRANCH;\n"
        "pub use bindings::OPC_BROWSE_DOWN as DA_BROWSE_DOWN;\n"
        "pub use bindings::OPC_FLAT as DA_BROWSE_FLAT;\n"
        "pub use bindings::OPC_BROWSE_TO as DA_BROWSE_TO;\n"
        "pub use bindings::OPC_BROWSE_UP as DA_BROWSE_UP;\n"
        "pub use bindings::OPC_READABLE as DA_ACCESS_READABLE;\n\n"
        "// Generated Windows ABI symbols are private implementation details.\n",
    )


if __name__ == "__main__":
    main()
