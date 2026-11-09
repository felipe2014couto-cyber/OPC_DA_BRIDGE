#![cfg(windows)]

mod configured;
mod com_lab_checks;

use std::collections::HashMap;
use std::fs::OpenOptions;
use std::io::Write;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, SystemTime};

use opc_classic_types::{ComObject, Error, ErrorCode, Guid, Result, Timestamp, Value, ValueType};
use opc_classic_utils::ComApartment;
use opc_classic_utils::server::{ClassFactory, LocalClassRegistration};
use opc_da_bindings::client::{
    ClientItemHandle, DaClient, DataSource, GroupOptions, ItemSpec, ServerItemHandle, ServerState,
    ServerStatus, WriteValue,
};
use opc_da_bindings::server::{
    BrowseItem, DaGroupService, DaServer, DaService, ServerAddedItem, ServerItemResult,
    ServerSample,
};
use opc_da_bindings::{
    DA_ACCESS_READABLE, DA_BROWSE_FLAT, DA_BROWSE_UP, DaBrowseServerAddressSpace,
};
use opc_da_lab_model::{
    CoreError, GroupHandle, ItemHandle, LabSimulator, ServerCore, SourceTimestamp, TagId, TagValue,
};
use windows::Win32::System::Com::{
    CLSCTX_LOCAL_SERVER, CoGetClassObject, CoTaskMemFree, CreateItemMoniker, GetRunningObjectTable,
    IEnumString, ROTFLAGS_REGISTRATIONKEEPSALIVE,
};
use windows_core::{IUnknown, Interface};

const SERVER_CLSID: Guid = Guid::new(
    0x7a2f_45b1,
    0x64d3,
    0x4e8c,
    [0x9a, 0x11, 0x3c, 0x85, 0x2b, 0x7f, 0x21, 0x40],
);
const SOURCE_LAB_CLSID: Guid = Guid::new(
    0xc748_2207,
    0x6725,
    0x496d,
    [0xb8, 0x46, 0xa0, 0xb3, 0x1b, 0x94, 0x19, 0x37],
);
fn is_source_lab_server() -> bool {
    std::env::args().any(|arg| arg == "--source-lab-server")
}
fn source_lab_stop_flag() -> Option<std::path::PathBuf> {
    if !is_source_lab_server() { return None; }
    std::env::current_exe().ok()
        .and_then(|path| path.parent().map(|parent| parent.join("rust-da-source-stop.flag")))
}
fn read_source_lab_override() -> std::result::Result<Option<(f64, bool)>, String> {
    let path = std::env::current_exe()
        .ok()
        .and_then(|exe| exe.parent().map(|dir| dir.join("rust-da-source-values.json")))
        .ok_or_else(|| "source lab folder unavailable".to_string())?;
    if !path.exists() { return Ok(None); }
    let bytes = std::fs::read(path).map_err(|_| "source lab sample unreadable".to_string())?;
    let document: serde_json::Value = serde_json::from_slice(&bytes)
        .map_err(|_| "source lab sample is invalid JSON".to_string())?;
    let object = document.as_object().ok_or_else(|| "source lab sample must be an object".to_string())?;
    if object.len() != 2 { return Err("source lab sample must contain only numeric and boolean".into()); }
    let numeric = object.get("numeric").and_then(serde_json::Value::as_f64)
        .filter(|value| value.is_finite()).ok_or_else(|| "source lab numeric value must be finite".to_string())?;
    let boolean = object.get("boolean").and_then(serde_json::Value::as_bool)
        .ok_or_else(|| "source lab boolean value must be true or false".to_string())?;
    Ok(Some((numeric, boolean)))
}
// Separate transient class for integration tests; never written to the Registry.
fn runtime_clsid() -> Guid {
    if is_source_lab_server() {
        SOURCE_LAB_CLSID
    } else if std::env::args().any(|a| a == "--isolated-source-test") {
        Guid::new(0x71e7a018, 0xb13b, 0x4e24, [0x94,0x61,0x5c,0x36,0xa7,0xe6,0x09,0x52])
    } else { SERVER_CLSID }
}
const IPC_TEST_MONIKER: &str = "RustDaBridge.Lab.IpcDiagnostic";
const REGISTRY_WARNING: &str = "This executable does not write persistent COM registration. It only registers its class object for this process.";

struct LabDaService {
    core: Arc<Mutex<ServerCore>>,
    start: Timestamp,
    groups: Mutex<HashMap<u32, GroupHandle>>,
}

impl LabDaService {
    fn new() -> Result<Arc<Self>> {
        let now = Timestamp::from_system_time(SystemTime::now())?;
        let mut core = ServerCore::new();
        let mut source = LabSimulator::new();
        source
            .publish_next(&mut core, SourceTimestamp::ValidFileTime(now.ticks()))
            .map_err(core_error)?;
        Ok(Arc::new(Self {
            core: Arc::new(Mutex::new(core)),
            start: now,
            groups: Mutex::new(HashMap::new()),
        }))
    }
}

impl DaService for LabDaService {
    fn browse_items(&self) -> Vec<BrowseItem> {
        let items = vec![
            BrowseItem {
                item_id: "RustLab.Numeric.Double".into(),
                data_type: 5,     // VT_R8
                access_rights: 1, // OPC_READABLE
            },
            BrowseItem {
                item_id: "RustLab.Boolean".into(),
                data_type: 11,    // VT_BOOL
                access_rights: 1, // OPC_READABLE
            },
        ];
        log_line(
            "BROWSE",
            &format!(
                "service item list requested; returning {} flat leaves",
                items.len()
            ),
        );
        items
    }

    fn status(&self) -> Result<ServerStatus> {
        let now = Timestamp::from_system_time(SystemTime::now())?;
        let group_count = self
            .groups
            .lock()
            .map_err(|_| Error::unexpected("group map poisoned"))?
            .len() as u32;
        Ok(ServerStatus {
            start_time: self.start,
            current_time: now,
            last_update_time: self.start,
            state: ServerState::Running,
            group_count,
            bandwidth: 0,
            version: (0, 1, 0),
            vendor_info: "Rust OPC DA Lab (simulated values)".into(),
        })
    }

    fn add_group(
        &self,
        server_handle: u32,
        options: GroupOptions,
    ) -> Result<Arc<dyn DaGroupService>> {
        log_line(
            "ADD_GROUP",
            &format!(
                "server_handle={} name={:?} active={} requested_update_rate_ms={}",
                server_handle, options.name, options.active, options.requested_update_rate
            ),
        );
        let mut core = self
            .core
            .lock()
            .map_err(|_| Error::unexpected("core poisoned"))?;
        let handle = core
            .add_group(options.name, options.active, options.requested_update_rate)
            .map_err(core_error)?;
        self.groups
            .lock()
            .map_err(|_| Error::unexpected("group map poisoned"))?
            .insert(server_handle, handle);
        Ok(Arc::new(LabDaGroup {
            core: self.core.clone(),
            group: handle,
            items: Mutex::new(HashMap::new()),
            item_types: Mutex::new(HashMap::new()),
            client_handles: Mutex::new(HashMap::new()),
            item_active: Mutex::new(HashMap::new()),
        }))
    }

    fn remove_group(&self, server_handle: u32, _force: bool) -> Result<()> {
        log_line(
            "REMOVE_GROUP",
            &format!("server_handle={} force={}", server_handle, _force),
        );
        let handle = self
            .groups
            .lock()
            .map_err(|_| Error::unexpected("group map poisoned"))?
            .remove(&server_handle)
            .ok_or_else(|| Error::invalid_argument("unknown group"))?;
        self.core
            .lock()
            .map_err(|_| Error::unexpected("core poisoned"))?
            .remove_group(handle)
            .map_err(core_error)
    }

    fn error_string(&self, error: ErrorCode, _locale: u32) -> Result<String> {
        Ok(format!("Rust DA laboratory error {error}"))
    }
}

struct LabDaGroup {
    core: Arc<Mutex<ServerCore>>,
    group: GroupHandle,
    items: Mutex<HashMap<u32, ItemHandle>>,
    item_types: Mutex<HashMap<u32, ValueType>>,
    client_handles: Mutex<HashMap<u32, u32>>,
    item_active: Mutex<HashMap<u32, bool>>,
}

impl DaGroupService for LabDaGroup {
    fn diagnostic(&self, event: &str) {
        log_line("COM_SYNCIO", event);
    }

    fn validate_read_handles(&self, handles: &[ServerItemHandle]) -> Result<Vec<ServerItemResult<ClientItemHandle>>> {
        let items = self.items.lock().map_err(|_| Error::unexpected("item map poisoned"))?;
        let clients = self.client_handles.lock().map_err(|_| Error::unexpected("client map poisoned"))?;
        Ok(handles.iter().map(|h| match (items.get(&h.raw()), clients.get(&h.raw())) {
            (Some(_), Some(client)) => ServerItemResult::success(ClientItemHandle(*client)),
            _ => ServerItemResult::failure(ErrorCode::from_raw(0xc0040001u32 as i32)),
        }).collect())
    }

    fn active_refresh_items(&self) -> Result<Vec<(ServerItemHandle, ClientItemHandle)>> {
        let active = self
            .item_active
            .lock()
            .map_err(|_| Error::unexpected("item active map poisoned"))?;
        let clients = self
            .client_handles
            .lock()
            .map_err(|_| Error::unexpected("client handle map poisoned"))?;
        let mut items: Vec<_> = active
            .iter()
            .filter(|(_, enabled)| **enabled)
            .filter_map(|(handle, _)| {
                clients.get(handle).map(|client| {
                    (ServerItemHandle::from_raw(*handle), ClientItemHandle(*client))
                })
            })
            .collect();
        items.sort_by_key(|(handle, _)| handle.raw());
        Ok(items)
    }

    fn add_items(&self, items: &[ItemSpec]) -> Result<Vec<ServerItemResult<ServerAddedItem>>> {
        let mut output = Vec::with_capacity(items.len());
        for item in items {
            log_line(
                "ADD_ITEM_REQUEST",
                &format!(
                    "item_id={:?} client_handle={} active={} requested_type={:?}",
                    item.item_id, item.client_handle.0, item.active, item.requested_data_type
                ),
            );
            let added = (|| {
                let kind = match item.item_id.as_str() {
                    "RustLab.Numeric.Double" => ValueType::F64,
                    "RustLab.Boolean" => ValueType::BOOL,
                    _ => return Err(Error::invalid_argument("unknown laboratory item")),
                };
                if item.requested_data_type != ValueType::EMPTY && item.requested_data_type != kind
                {
                    return Err(Error::from_code(ErrorCode::from_raw(0xC0040004_u32 as i32)));
                }
                let mut core = self
                    .core
                    .lock()
                    .map_err(|_| Error::unexpected("core poisoned"))?;
                let handle = core
                    .add_item(self.group, &item.item_id, item.client_handle.0)
                    .map_err(core_error)?;
                self.items
                    .lock()
                    .map_err(|_| Error::unexpected("item map poisoned"))?
                    .insert(handle.get(), handle);
                self.item_types
                    .lock()
                    .map_err(|_| Error::unexpected("item type map poisoned"))?
                    .insert(handle.get(), kind);
                self.client_handles
                    .lock()
                    .map_err(|_| Error::unexpected("client handle map poisoned"))?
                    .insert(handle.get(), item.client_handle.0);
                self.item_active
                    .lock()
                    .map_err(|_| Error::unexpected("item active map poisoned"))?
                    .insert(handle.get(), item.active);
                Ok(ServerAddedItem {
                    server_handle: ServerItemHandle::from_raw(handle.get()),
                    canonical_data_type: kind,
                    access_rights: 1,
                    blob: Vec::new(),
                })
            })();
            let result = match added {
                Ok(item) => ServerItemResult::success(item),
                Err(error) => ServerItemResult::failure(error.code()),
            };
            match &result.result {
                Ok(added_item) => log_line(
                    "ADD_ITEM_RESULT",
                    &format!(
                        "item_id={:?} server_handle={} canonical_type={:?} access_rights={} result=S_OK",
                        item.item_id,
                        added_item.server_handle.raw(),
                        added_item.canonical_data_type,
                        added_item.access_rights
                    ),
                ),
                Err(code) => log_line(
                    "ADD_ITEM_RESULT",
                    &format!(
                        "item_id={:?} hresult={:#010x}",
                        item.item_id,
                        code.raw() as u32
                    ),
                ),
            }
            output.push(result);
        }
        Ok(output)
    }

    fn remove_items(&self, handles: &[ServerItemHandle]) -> Result<Vec<ServerItemResult<()>>> {
        let mut output = Vec::with_capacity(handles.len());
        for handle in handles {
            let item = self
                .items
                .lock()
                .map_err(|_| Error::unexpected("item map poisoned"))?
                .remove(&handle.raw());
            if item.is_some() {
                if let Ok(mut values) = self.item_types.lock() {
                    values.remove(&handle.raw());
                }
                if let Ok(mut values) = self.client_handles.lock() {
                    values.remove(&handle.raw());
                }
                if let Ok(mut values) = self.item_active.lock() {
                    values.remove(&handle.raw());
                }
            }
            let result = match item {
                Some(item) => self
                    .core
                    .lock()
                    .map_err(|_| Error::unexpected("core poisoned"))?
                    .remove_item(self.group, item)
                    .map_err(core_error),
                None => Err(Error::invalid_argument("unknown item handle")),
            };
            output.push(result.map_or_else(
                |error| ServerItemResult::failure(error.code()),
                |_| ServerItemResult::success(()),
            ));
        }
        Ok(output)
    }

    fn set_active(
        &self,
        _handles: &[ServerItemHandle],
        active: bool,
    ) -> Result<Vec<ServerItemResult<()>>> {
        let mut states = self
            .item_active
            .lock()
            .map_err(|_| Error::unexpected("item active map poisoned"))?;
        Ok(_handles
            .iter()
            .map(|handle| match states.get_mut(&handle.raw()) {
                Some(state) => {
                    *state = active;
                    ServerItemResult::success(())
                }
                None => ServerItemResult::failure(ErrorCode::INVALID_ARGUMENT),
            })
            .collect())
    }

    fn set_client_handles(
        &self,
        handles: &[(ServerItemHandle, ClientItemHandle)],
    ) -> Result<Vec<ServerItemResult<()>>> {
        let mut clients = self
            .client_handles
            .lock()
            .map_err(|_| Error::unexpected("client handle map poisoned"))?;
        Ok(handles
            .iter()
            .map(|(handle, client)| match clients.get_mut(&handle.raw()) {
                Some(value) => {
                    *value = client.0;
                    ServerItemResult::success(())
                }
                None => ServerItemResult::failure(ErrorCode::INVALID_ARGUMENT),
            })
            .collect())
    }

    fn set_data_types(
        &self,
        handles: &[(ServerItemHandle, ValueType)],
    ) -> Result<Vec<ServerItemResult<()>>> {
        let types = self
            .item_types
            .lock()
            .map_err(|_| Error::unexpected("item type map poisoned"))?;
        Ok(handles
            .iter()
            .map(|(handle, requested)| {
                let expected = types.get(&handle.raw()).copied();
                if expected == Some(*requested) || *requested == ValueType::EMPTY {
                    ServerItemResult::success(())
                } else {
                    ServerItemResult::failure(ErrorCode::from_raw(0xC0040004_u32 as i32))
                }
            })
            .collect())
    }

    fn read(
        &self,
        source: DataSource,
        handles: &[ServerItemHandle],
    ) -> Result<Vec<ServerItemResult<ServerSample>>> {
        log_line(
            "READ_REQUEST",
            &format!(
                "source={:?} count={} handles={:?}",
                source,
                handles.len(),
                handles.iter().map(|h| h.raw()).collect::<Vec<_>>()
            ),
        );
        let items = self
            .items
            .lock()
            .map_err(|_| Error::unexpected("item map poisoned"))?;
        let clients = self
            .client_handles
            .lock()
            .map_err(|_| Error::unexpected("client handle map poisoned"))?;
        let core = self
            .core
            .lock()
            .map_err(|_| Error::unexpected("core poisoned"))?;
        let core_handles: Vec<_> = handles
            .iter()
            .filter_map(|handle| items.get(&handle.raw()).copied())
            .collect();
        let reads = core.read(self.group, &core_handles).map_err(core_error)?;
        let mut by_handle = reads
            .into_iter()
            .map(|read| (read.item_handle.get(), read))
            .collect::<HashMap<_, _>>();
        let output = handles
            .iter()
            .map(|handle| {
                let result = by_handle
                    .remove(&handle.raw())
                    .ok_or_else(|| Error::invalid_argument("unknown item handle"))
                    .and_then(|read| {
                        let snapshot = read.result.map_err(core_error)?;
                        let value = match snapshot.value {
                            Some(TagValue::Double(value)) => Value::F64(value),
                            Some(TagValue::Boolean(value)) => Value::Bool(value),
                            None => {
                                return Err(Error::from_code(ErrorCode::from_raw(
                                    0xC0040008_u32 as i32,
                                )))
                            }
                        };
                        let quality = match snapshot.quality {
                            opc_da_lab_model::Quality::Good => 0xC0,
                            opc_da_lab_model::Quality::UncertainTimestampMissing
                            | opc_da_lab_model::Quality::UncertainTimestampInvalid => 0x40,
                            opc_da_lab_model::Quality::BadNoSample => 0x20,
                            opc_da_lab_model::Quality::BadNotConnected => 0x08,
                        };
                        Ok(ServerSample {
                            client_handle: ClientItemHandle(
                                clients
                                    .get(&handle.raw())
                                    .copied()
                                    .unwrap_or(read.client_handle),
                            ),
                            timestamp: Timestamp::from_ticks(
                                snapshot.source_timestamp_filetime.unwrap_or(0),
                            ),
                            quality,
                            value,
                        })
                    });
                let result = result.map_or_else(
                    |error| ServerItemResult::failure(error.code()),
                    ServerItemResult::success,
                );
                match &result.result {
                    Ok(sample) => log_line(
                        "READ_RESULT",
                        &format!(
                            "server_handle={} client_handle={} value={:?} quality={:#04x} filetime={} result=S_OK",
                            handle.raw(), sample.client_handle.0, sample.value,
                            sample.quality, sample.timestamp.ticks()
                        ),
                    ),
                    Err(code) => log_line(
                        "READ_RESULT",
                        &format!("server_handle={} hresult={:#010x}", handle.raw(), code.raw() as u32),
                    ),
                }
                result
            })
            .collect();
        Ok(output)
    }

    fn write(&self, values: &[(ServerItemHandle, Value)]) -> Result<Vec<ServerItemResult<()>>> {
        Ok(values
            .iter()
            .map(|_| ServerItemResult::failure(ErrorCode::from_raw(0xC0040006_u32 as i32)))
            .collect())
    }
}

fn core_error(error: CoreError) -> Error {
    match error {
        CoreError::WriteRejectedReadOnly => {
            Error::from_code(ErrorCode::from_raw(0xC0040006_u32 as i32))
        }
        CoreError::UnknownItemId | CoreError::ItemNotFound | CoreError::GroupNotFound => {
            Error::invalid_argument("unknown OPC DA item or group")
        }
        CoreError::WrongType { .. } => Error::from_code(ErrorCode::from_raw(0xC0040004_u32 as i32)),
        CoreError::SourceUnavailable => {
            Error::from_code(ErrorCode::from_raw(0xC0040008_u32 as i32))
        }
        CoreError::GroupNameInUse
        | CoreError::HandleSpaceExhausted
        | CoreError::SimulationSequenceExhausted => {
            Error::unexpected("laboratory server state error")
        }
    }
}

fn main() {
    if std::env::args().any(|a|a=="--async-client-test" || a=="--async-project-client-test" || a=="--kepware-timestamp-check") {
        let result=if std::env::args().any(|a|a=="--kepware-timestamp-check") {com_lab_checks::kepware_timestamp_check()} else {com_lab_checks::async_test()};
        if let Err(e)=result {eprintln!("COM probe failed: {e}");std::process::exit(1);} return;
    }
    if std::env::args().any(|arg| arg == "--validate-project") {
        let result = (|| -> Result<()> {
            let path = configured::project_path()?;
            let project = opc_da_project::Project::load(&path).map_err(Error::invalid_argument)?;
            let bytes = std::fs::read(&path).map_err(|e| Error::unexpected(e.to_string()))?;
            let digest = opc_da_project::sha256(&bytes).map_err(Error::unexpected)?;
            println!("{}", serde_json::json!({"ok":true,"project_sha256":digest,"variable_count":project.published_variables().len(),"deployment":project.deployment}));
            Ok(())
        })();
        if let Err(error) = result { eprintln!("Project validation failed: {error}"); std::process::exit(1); }
        return;
    }
    if std::env::args().any(|arg| arg == "/RegServer" || arg == "--register") {
        eprintln!("Persistent registration is intentionally disabled. {REGISTRY_WARNING}");
        std::process::exit(2);
    }
    if std::env::args().any(|arg| arg == "--project-client-test") {
        if let Err(error) = configured::client_test() { eprintln!("Configured cross-process COM test failed: {error}"); std::process::exit(1); }
        return;
    }
    if std::env::args().any(|arg| arg == "--self-test") {
        if let Err(error) = run_self_test() {
            eprintln!("COM self-test failed: {error}");
            std::process::exit(1);
        }
        return;
    }
    if std::env::args().any(|arg| arg == "--browse-self-test") {
        if let Err(error) = run_browse_self_test() {
            eprintln!("COM browse self-test failed: {error}");
            std::process::exit(1);
        }
        return;
    }
    if std::env::args().any(|arg| arg == "--ipc-client-test") {
        if let Err(error) = run_ipc_client_test() {
            eprintln!("cross-process COM test failed: {error}");
            std::process::exit(1);
        }
        return;
    }
    if std::env::args().any(|arg| arg == "--source-lab-client-test") {
        if let Err(error) = run_source_lab_client_test() {
            eprintln!("source-lab cross-process COM test failed: {error}");
            std::process::exit(1);
        }
        return;
    }
    if std::env::args().any(|arg| arg == "--ipc-test-server") {
        if let Err(error) = run_rot_diagnostic_server(test_shutdown_path()) {
            eprintln!("cross-process COM test server failed: {error}");
            std::process::exit(1);
        }
        return;
    }
    if !std::env::args()
        .any(|arg| arg == "/Embedding" || arg == "-Embedding" || arg == "--volatile-server"
            || arg == "--source-lab-server")
    {
        println!("Rust OPC DA COM lab prototype. Use --self-test for volatile COM checks.");
        println!("CLSID: {{7A2F45B1-64D3-4E8C-9A11-3C852B7F2140}}");
        println!("Separate source fixture: --source-lab-server -Embedding (CLSID {{C7482207-6725-496D-B846-A0B31B941937}}).");
        return;
    }
    let test_stop = if std::env::args().any(|a|a=="--async-test-server") {
        if !std::env::args().any(|a|a=="--isolated-source-test") {eprintln!("Async test server requires isolated laboratory CLSID");std::process::exit(2);}
        std::env::current_exe().ok().map(|p|p.with_file_name("async-test-stop.flag"))
    } else {None};
    if let Err(error) = run_server(runtime_clsid(), source_lab_stop_flag().or(test_stop)) {
        log_line("ERROR", &format!("server startup/runtime failed: {error}"));
        eprintln!("COM lab server failed: {error}");
        std::process::exit(1);
    }
}

fn run_source_lab_client_test() -> Result<()> {
    let apartment = ComApartment::mta()?;
    let class_id = windows_core::GUID::from_values(
        SOURCE_LAB_CLSID.data1,
        SOURCE_LAB_CLSID.data2,
        SOURCE_LAB_CLSID.data3,
        SOURCE_LAB_CLSID.data4,
    );
    let factory: windows::Win32::System::Com::IClassFactory =
        unsafe { CoGetClassObject(&class_id, CLSCTX_LOCAL_SERVER, None) }
            .map_err(|error| Error::unexpected(format!("source CLSID activation failed: {error}")))?;
    let unknown: windows_core::IUnknown = unsafe {
        factory.CreateInstance(None::<&windows_core::IUnknown>)
    }.map_err(|error| Error::unexpected(format!("source CreateInstance failed: {error}")))?;
    let object = unsafe { ComObject::from_raw_owned(unknown.into_raw())? };
    let client = DaClient::from_object(&apartment, &object)?;
    let status = client.status()?;
    verify_browse(&client.object())?;
    let group = client.add_group(GroupOptions::new("RustDaSourceLabCrossProcess"))?;
    let added = group.add_items(&[
        ItemSpec::new("RustLab.Numeric.Double", 301),
        ItemSpec::new("RustLab.Boolean", 302),
    ])?;
    let handles = added.into_iter().map(|entry| {
        entry.map(|item| item.server_handle)
            .map_err(|error| Error::unexpected(format!("source AddItems HRESULT={:#010x}", error.code.raw() as u32)))
    }).collect::<Result<Vec<_>>>()?;
    let samples = group.read(DataSource::Device, &handles)?;
    let samples = samples.into_iter().map(|sample| {
        sample.map_err(|error| Error::unexpected(format!("source SyncRead HRESULT={:#010x}", error.code.raw() as u32)))
    }).collect::<Result<Vec<_>>>()?;
    for (index, sample) in samples.iter().enumerate() {
        println!("SOURCE_SAMPLE index={} variant={:?} quality={:#06x} timestamp_filetime={} read_hresult=S_OK method=IOPCSyncIO::Read",
                 index, sample.value, sample.quality, sample.timestamp.ticks());
    }
    if samples.len() != 2
        || samples.iter().any(|sample| sample.quality != 0xc0 || sample.timestamp.ticks() == 0) {
        return Err(Error::unexpected("source COM read returned invalid type, quality or timestamp"));
    }
    if !matches!(samples[0].value, Value::F64(value) if value.is_finite())
        || !matches!(samples[1].value, Value::Bool(_)) {
        return Err(Error::unexpected("source COM read returned an unexpected VARIANT type"));
    }
    println!("SOURCE_LAB_COM_CROSS_PROCESS_PASS state={:?} numeric={:?} boolean={:?} quality=0x00c0 timestamps_valid=true",
             status.state, samples[0].value, samples[1].value);
    Ok(())
}

fn run_ipc_client_test() -> Result<()> {
    let apartment = ComApartment::mta()?;
    println!("IPC_CLIENT_START pid={}", std::process::id());
    let rot = unsafe { GetRunningObjectTable(0) }
        .map_err(|error| Error::unexpected(format!("GetRunningObjectTable failed: {error}")))?;
    let moniker = unsafe {
        CreateItemMoniker(
            windows_core::w!("!"),
            windows_core::w!("RustDaBridge.Lab.IpcDiagnostic"),
        )
    }
    .map_err(|error| Error::unexpected(format!("CreateItemMoniker failed: {error}")))?;
    let unknown = unsafe { rot.GetObject(&moniker) }.map_err(|error| {
        Error::unexpected(format!(
            "ROT GetObject (cross-process marshal) failed: {error}"
        ))
    })?;
    let server_object = unsafe { ComObject::from_raw_owned(unknown.into_raw())? };
    println!("ROT_GETOBJECT_PASS cross_process_interface_obtained=true");
    let client = DaClient::from_object(&apartment, &server_object)?;
    let status = client.status()?;
    println!(
        "SERVER_STATUS state={:?} groups={} vendor={:?}",
        status.state, status.group_count, status.vendor_info
    );
    verify_browse(&client.object())?;
    let group = client.add_group(GroupOptions::new("RustLabIpcProcessTest"))?;
    let added = group.add_items(&[
        ItemSpec::new("RustLab.Numeric.Double", 201),
        ItemSpec::new("RustLab.Boolean", 202),
    ])?;
    let handles = added
        .into_iter()
        .enumerate()
        .map(|(index, item)| {
            item.map(|item| {
                println!(
                    "ADD_ITEM index={} server_handle={} vt={:?} rights={:#x}",
                    index,
                    item.server_handle.raw(),
                    item.canonical_data_type,
                    item.access_rights
                );
                item.server_handle
            })
            .map_err(|error| {
                Error::unexpected(format!(
                    "AddItems[{index}] HRESULT={:#010x}",
                    error.code.raw() as u32
                ))
            })
        })
        .collect::<Result<Vec<_>>>()?;
    let samples = group.read(DataSource::Device, &handles)?;
    for (index, sample) in samples.into_iter().enumerate() {
        let sample = sample.map_err(|error| {
            Error::unexpected(format!(
                "Read[{index}] HRESULT={:#010x}",
                error.code.raw() as u32
            ))
        })?;
        println!(
            "READ index={} client_handle={} value={:?} quality={:#04x} timestamp_filetime={}",
            index,
            sample.client_handle.0,
            sample.value,
            sample.quality,
            sample.timestamp.ticks()
        );
    }
    group.close(true)?;
    println!(
        "IPC_CLIENT_PASS distinct_process_com=ROT_marshaled read=sync_only advise=not_called write=not_called"
    );
    Ok(())
}

fn test_shutdown_path() -> std::path::PathBuf {
    std::env::current_exe()
        .ok()
        .and_then(|path| {
            path.parent()
                .map(|parent| parent.join("ipc-test-stop.flag"))
        })
        .unwrap_or_else(|| std::path::PathBuf::from("ipc-test-stop.flag"))
}

fn run_rot_diagnostic_server(shutdown_flag: std::path::PathBuf) -> Result<()> {
    if shutdown_flag.exists() {
        return Err(Error::invalid_argument(
            "refusing IPC test startup while its shutdown marker already exists",
        ));
    }
    let _apartment = ComApartment::mta()?;
    let service = LabDaService::new()?;
    let object = DaServer::new(service.clone()).object();
    let unknown = unsafe { IUnknown::from_raw(object.into_raw()) };
    let moniker = unsafe {
        CreateItemMoniker(
            windows_core::w!("!"),
            windows_core::w!("RustDaBridge.Lab.IpcDiagnostic"),
        )
    }
    .map_err(|error| Error::unexpected(format!("CreateItemMoniker failed: {error}")))?;
    let rot = unsafe { GetRunningObjectTable(0) }
        .map_err(|error| Error::unexpected(format!("GetRunningObjectTable failed: {error}")))?;
    let cookie = unsafe { rot.Register(ROTFLAGS_REGISTRATIONKEEPSALIVE, &unknown, &moniker) }
        .map_err(|error| Error::unexpected(format!("ROT Register failed: {error}")))?;
    log_line(
        "IPC_TEST",
        &format!(
            "COM object registered in ROT under item moniker {IPC_TEST_MONIKER}; no registry keys written"
        ),
    );

    let monitor_core = service.core.clone();
    let stop = Arc::new(AtomicBool::new(false));
    let thread_stop = stop.clone();
    let source_flag = std::env::var_os("RUST_DA_LAB_SOURCE_FLAG")
        .unwrap_or_else(|| "rust-da-source-offline.flag".into());
    let monitor = std::thread::spawn(move || {
        let mut source = LabSimulator::new();
        let mut was_available = true;
        while !thread_stop.load(Ordering::Relaxed) {
            let available = !std::path::Path::new(&source_flag).exists();
            if available != was_available {
                if let Ok(mut core) = monitor_core.lock() {
                    core.set_source_available(available);
                }
                was_available = available;
                log_line(
                    "SOURCE",
                    if available {
                        "available; next sample will restore quality"
                    } else {
                        "unavailable; cached quality degraded"
                    },
                );
            }
            if available {
                if let Ok(timestamp) = Timestamp::from_system_time(SystemTime::now()) {
                    if let Ok(mut core) = monitor_core.lock() {
                        if let Err(error) = source.publish_next(
                            &mut core,
                            SourceTimestamp::ValidFileTime(timestamp.ticks()),
                        ) {
                            log_line(
                                "ERROR",
                                &format!("simulated sample failed: {}", core_error(error)),
                            );
                        }
                    }
                }
            }
            std::thread::sleep(Duration::from_millis(250));
        }
    });
    log_line(
        "IPC_TEST",
        &format!("awaiting shutdown marker {}", shutdown_flag.display()),
    );
    while !shutdown_flag.exists() {
        std::thread::sleep(Duration::from_millis(250));
    }
    stop.store(true, Ordering::Relaxed);
    monitor
        .join()
        .map_err(|_| Error::unexpected("simulator thread panicked"))?;
    unsafe { rot.Revoke(cookie) }
        .map_err(|error| Error::unexpected(format!("ROT Revoke failed: {error}")))?;
    log_line(
        "STOP",
        "ROT diagnostic object revoked and server stopped by marker",
    );
    println!("IPC_TEST_SERVER_STOPPED");
    let _ = std::fs::remove_file(shutdown_flag);
    Ok(())
}

fn run_browse_self_test() -> Result<()> {
    let _apartment = ComApartment::mta()?;
    let service = LabDaService::new()?;
    let server_object = DaServer::new(service).object();
    verify_browse(&server_object)
}

fn run_self_test() -> Result<()> {
    let apartment = ComApartment::mta()?;
    let service = LabDaService::new()?;
    let factory_service = service.clone();
    let factory = ClassFactory::new(move || Ok(DaServer::new(factory_service.clone()).object()));
    // Use an ephemeral identity: the laboratory CLSID now has persistent
    // registration, so probing it after revocation would launch the registered
    // executable instead of proving this test's class object was revoked.
    let temporary_id = unsafe { windows::Win32::System::Com::CoCreateGuid() }
        .map_err(|error| Error::unexpected(error.to_string()))?;
    let test_class_id = Guid::new(temporary_id.data1, temporary_id.data2, temporary_id.data3, temporary_id.data4);
    let registration = LocalClassRegistration::register(&apartment, &test_class_id, factory)?;
    println!("COM apartment initialized and class object registered in memory.");

    let class_id = windows_core::GUID::from_values(
        test_class_id.data1,
        test_class_id.data2,
        test_class_id.data3,
        test_class_id.data4,
    );
    let class_factory: windows::Win32::System::Com::IClassFactory =
        unsafe { CoGetClassObject(&class_id, CLSCTX_LOCAL_SERVER, None) }
            .map_err(|error| Error::unexpected(format!("CoGetClassObject failed: {error}")))?;
    let instance: windows_core::IUnknown =
        unsafe { class_factory.CreateInstance(None::<&windows_core::IUnknown>) }.map_err(
            |error| Error::unexpected(format!("IClassFactory.CreateInstance failed: {error}")),
        )?;
    query_iopc_server(&instance)?;
    drop(instance);
    drop(class_factory);
    println!("CoGetClassObject, CreateInstance and IOPCServer QueryInterface passed.");

    let server_object = DaServer::new(service.clone()).object();
    verify_browse(&server_object)?;
    let client = DaClient::from_object(&apartment, &server_object)?;
    let group = client.add_group(GroupOptions::new("RustLabSelfTest"))?;
    let added = group.add_items(&[
        ItemSpec::new("RustLab.Numeric.Double", 101),
        ItemSpec::new("RustLab.Boolean", 102),
    ])?;
    let handles = added
        .into_iter()
        .map(|item| {
            item.map(|item| item.server_handle)
                .map_err(|error| Error::unexpected(format!("AddItems failed: {error}")))
        })
        .collect::<Result<Vec<_>>>()?;
    if handles.len() != 2 {
        return Err(Error::unexpected("expected two simulated items"));
    }

    let initial = group.read(DataSource::Cache, &handles)?;
    let initial = initial
        .into_iter()
        .map(|sample| sample.map_err(|error| Error::unexpected(format!("Read failed: {error}"))))
        .collect::<Result<Vec<_>>>()?;
    if initial[0].value != Value::F64(12.5)
        || initial[1].value != Value::Bool(true)
        || initial
            .iter()
            .any(|sample| sample.quality != 0xC0 || sample.timestamp.ticks() == 0)
    {
        return Err(Error::unexpected(
            "initial COM read returned unexpected value, quality or timestamp",
        ));
    }
    println!("AddGroup, AddItems and synchronous reads passed for Double and Boolean.");

    let write = group.write(&[WriteValue {
        server_handle: handles[0],
        value: Value::F64(99.0),
    }])?;
    if write.len() != 1
        || write[0]
            .as_ref()
            .err()
            .map_or(true, |error| error.code.raw() != 0xC0040006_u32 as i32)
    {
        return Err(Error::unexpected(
            "write was not rejected with OPC_E_BADRIGHTS",
        ));
    }
    println!("Read-only write rejection passed.");

    service
        .core
        .lock()
        .map_err(|_| Error::unexpected("core poisoned"))?
        .set_source_available(false);
    let offline = group.read(DataSource::Cache, &handles)?;
    if offline.iter().any(|sample| {
        sample
            .as_ref()
            .map_or(true, |sample| sample.quality != 0x08)
    }) {
        return Err(Error::unexpected(
            "source loss did not map to OPC_QUALITY_NOT_CONNECTED",
        ));
    }
    service
        .core
        .lock()
        .map_err(|_| Error::unexpected("core poisoned"))?
        .set_source_available(true);
    let still_bad = group.read(DataSource::Cache, &handles)?;
    if still_bad.iter().any(|sample| {
        sample
            .as_ref()
            .map_or(true, |sample| sample.quality != 0x08)
    }) {
        return Err(Error::unexpected(
            "reconnection promoted stale values before a new sample",
        ));
    }
    let mut source = LabSimulator::new();
    let timestamp = Timestamp::from_system_time(SystemTime::now())?;
    let mut core = service
        .core
        .lock()
        .map_err(|_| Error::unexpected("core poisoned"))?;
    source
        .publish_next(&mut core, SourceTimestamp::ValidFileTime(timestamp.ticks()))
        .map_err(core_error)?;
    drop(core);
    let recovered = group.read(DataSource::Cache, &handles)?;
    if recovered.iter().any(|sample| {
        sample
            .as_ref()
            .map_or(true, |sample| sample.quality != 0xC0)
    }) {
        return Err(Error::unexpected(
            "new simulator samples did not restore Good quality",
        ));
    }
    println!("Source-loss degradation and fresh-sample recovery passed.");

    group.close(true)?;
    drop(client);
    drop(server_object);
    registration.revoke()?;
    let after_revoke: windows::core::Result<windows::Win32::System::Com::IClassFactory> =
        unsafe { CoGetClassObject(&class_id, CLSCTX_LOCAL_SERVER, None) };
    if let Ok(factory) = after_revoke {
        drop(factory);
        return Err(Error::unexpected(
            "CoRegisterClassObject revocation did not take effect",
        ));
    }
    println!("CoRevokeClassObject and COM apartment teardown passed.");
    Ok(())
}

fn query_iopc_server(instance: &windows_core::IUnknown) -> Result<()> {
    let iid = windows_core::GUID::from_values(
        0x39c1_3a4d,
        0x011e,
        0x11d0,
        [0x96, 0x75, 0x00, 0x20, 0xaf, 0xd8, 0xad, 0xb3],
    );
    let mut output = std::ptr::null_mut();
    let vtable =
        unsafe { *(Interface::as_raw(instance) as *const *const windows_core::IUnknown_Vtbl) };
    let hr = unsafe { ((*vtable).QueryInterface)(Interface::as_raw(instance), &iid, &mut output) };
    if hr.0 < 0 || output.is_null() {
        return Err(Error::unexpected(format!(
            "IOPCServer QueryInterface failed: {:#010x}",
            hr.0 as u32
        )));
    }
    drop(unsafe { windows_core::IUnknown::from_raw(output) });
    Ok(())
}

fn verify_browse(server_object: &opc_classic_types::ComObject) -> Result<()> {
    let browse_iid = Guid::new(
        0x39c1_3a4f,
        0x011e,
        0x11d0,
        [0x96, 0x75, 0x00, 0x20, 0xaf, 0xd8, 0xad, 0xb3],
    );
    let queried = server_object
        .query_interface(&browse_iid)
        .map_err(|error| {
            Error::unexpected(format!("IOPCBrowseServerAddressSpace QI failed: {error}"))
        })?;
    let browse = unsafe { DaBrowseServerAddressSpace::from_raw(queried.into_raw()) };
    let organization = unsafe { browse.QueryOrganization() }
        .map_err(|error| Error::unexpected(format!("QueryOrganization failed: {error}")))?;
    if organization.0 != 2 {
        return Err(Error::unexpected(
            "browse namespace is not reported as flat",
        ));
    }
    // Reproduce a flat-browser client which attempts to move upward until the
    // server signals that no browse position change is possible. A success
    // must not leave the client retrying at the unchanged root.
    let mut up_calls = 0;
    let mut navigation_stopped = false;
    for _ in 0..8 {
        up_calls += 1;
        match unsafe { browse.ChangeBrowsePosition(DA_BROWSE_UP, windows_core::w!("")) } {
            Ok(()) => continue,
            Err(error) if error.code().0 == 0x8000_4005_u32 as i32 => {
                navigation_stopped = true;
                break;
            }
            Err(error) => {
                return Err(Error::unexpected(format!(
                    "flat ChangeBrowsePosition(UP) returned unexpected HRESULT {:#010x}",
                    error.code().0 as u32
                )));
            }
        }
    }
    if !navigation_stopped || up_calls != 1 {
        return Err(Error::unexpected(format!(
            "flat UP retry sequence did not stop on its first call (calls={up_calls})"
        )));
    }
    println!(
        "FLAT_UP_SEQUENCE_PASS calls={up_calls} terminal_hresult=0x80004005 position=flat-root"
    );
    let above_root = unsafe { browse.ChangeBrowsePosition(DA_BROWSE_UP, windows_core::w!("")) };
    if above_root
        .as_ref()
        .err()
        .map_or(true, |error| error.code().0 != 0x8000_4005_u32 as i32)
    {
        return Err(Error::unexpected(
            "moving UP above the flat root did not return E_FAIL",
        ));
    }

    let enumerator: IEnumString = unsafe {
        browse.BrowseOPCItemIDs(DA_BROWSE_FLAT, windows_core::w!(""), 0, DA_ACCESS_READABLE)
    }
    .map_err(|error| Error::unexpected(format!("BrowseOPCItemIDs failed: {error}")))?;

    let mut first = windows_core::PWSTR::null();
    let mut fetched = 0;
    let first_status =
        unsafe { enumerator.Next(std::slice::from_mut(&mut first), Some(&mut fetched)) };
    if first_status.0 < 0 || fetched != 1 {
        return Err(Error::unexpected(
            "IEnumString did not return its first item",
        ));
    }
    let first_name = unsafe { first.to_string() };
    unsafe { CoTaskMemFree(Some(first.0.cast())) };
    let first_name = first_name
        .map_err(|error| Error::unexpected(format!("browse item UTF-16 was invalid: {error}")))?;
    if first_name != "RustLab.Boolean" {
        return Err(Error::unexpected(format!(
            "unexpected first browse item: {first_name}"
        )));
    }

    let no_fetched_enumerator = unsafe {
        browse.BrowseOPCItemIDs(DA_BROWSE_FLAT, windows_core::w!(""), 0, DA_ACCESS_READABLE)
    }
    .map_err(|error| {
        Error::unexpected(format!(
            "BrowseOPCItemIDs for pceltFetched test failed: {error}"
        ))
    })?;
    let mut no_fetched_name = windows_core::PWSTR::null();
    let no_fetched_status =
        unsafe { no_fetched_enumerator.Next(std::slice::from_mut(&mut no_fetched_name), None) };
    let no_fetched_name_result = unsafe { no_fetched_name.to_string() };
    unsafe { CoTaskMemFree(Some(no_fetched_name.0.cast())) };
    let no_fetched_name = no_fetched_name_result.map_err(|error| {
        Error::unexpected(format!(
            "IEnumString.Next without pceltFetched returned invalid UTF-16: {error}"
        ))
    })?;
    if no_fetched_status.0 != 0 || no_fetched_name != "RustLab.Boolean" {
        return Err(Error::unexpected(format!(
            "IEnumString.Next(celt=1, pceltFetched=NULL) failed: hr={:#010x}, item={no_fetched_name:?}",
            no_fetched_status.0 as u32
        )));
    }

    let cloned = unsafe { enumerator.Clone() }
        .map_err(|error| Error::unexpected(format!("IEnumString.Clone failed: {error}")))?;
    let mut next_original = windows_core::PWSTR::null();
    let mut next_clone = windows_core::PWSTR::null();
    let mut original_fetched = 0;
    let mut clone_fetched = 0;
    let original_status = unsafe {
        enumerator.Next(
            std::slice::from_mut(&mut next_original),
            Some(&mut original_fetched),
        )
    };
    let clone_status = unsafe {
        cloned.Next(
            std::slice::from_mut(&mut next_clone),
            Some(&mut clone_fetched),
        )
    };
    if original_status.0 < 0 || clone_status.0 < 0 || original_fetched != 1 || clone_fetched != 1 {
        return Err(Error::unexpected(
            "IEnumString clone did not preserve the enumeration position",
        ));
    }
    let original_name = unsafe { next_original.to_string() };
    let clone_name = unsafe { next_clone.to_string() };
    unsafe {
        CoTaskMemFree(Some(next_original.0.cast()));
        CoTaskMemFree(Some(next_clone.0.cast()));
    }
    let original_name = original_name
        .map_err(|error| Error::unexpected(format!("browse item UTF-16 was invalid: {error}")))?;
    let clone_name = clone_name
        .map_err(|error| Error::unexpected(format!("browse item UTF-16 was invalid: {error}")))?;
    if original_name != "RustLab.Numeric.Double" || clone_name != original_name {
        return Err(Error::unexpected(
            "browse enumeration returned unexpected item IDs",
        ));
    }

    let mut extra = windows_core::PWSTR::null();
    let mut extra_fetched = 0;
    let end_status =
        unsafe { enumerator.Next(std::slice::from_mut(&mut extra), Some(&mut extra_fetched)) };
    if end_status.0 != 1 || extra_fetched != 0 {
        return Err(Error::unexpected(
            "IEnumString did not report end of enumeration with S_FALSE",
        ));
    }
    unsafe { enumerator.Reset() }
        .map_err(|error| Error::unexpected(format!("IEnumString.Reset failed: {error}")))?;
    if unsafe { enumerator.Skip(1) }.0 != 0 {
        return Err(Error::unexpected("IEnumString.Skip failed before the end"));
    }
    let mut skipped_item = windows_core::PWSTR::null();
    let mut skipped_count = 0;
    let skipped_status = unsafe {
        enumerator.Next(
            std::slice::from_mut(&mut skipped_item),
            Some(&mut skipped_count),
        )
    };
    let skipped_name = unsafe { skipped_item.to_string() };
    unsafe { CoTaskMemFree(Some(skipped_item.0.cast())) };
    let skipped_name = skipped_name
        .map_err(|error| Error::unexpected(format!("browse item UTF-16 was invalid: {error}")))?;
    if skipped_status.0 < 0 || skipped_count != 1 || skipped_name != "RustLab.Numeric.Double" {
        return Err(Error::unexpected(
            "IEnumString.Reset/Skip returned the wrong item",
        ));
    }

    let boolean_id = windows_core::w!("RustLab.Boolean");
    let resolved = unsafe { browse.GetItemID(boolean_id) }
        .map_err(|error| Error::unexpected(format!("GetItemID failed: {error}")))?;
    let resolved_name = unsafe { resolved.to_string() };
    unsafe { CoTaskMemFree(Some(resolved.0.cast())) };
    let resolved_name = resolved_name.map_err(|error| {
        Error::unexpected(format!("GetItemID returned invalid UTF-16: {error}"))
    })?;
    if resolved_name != "RustLab.Boolean" {
        return Err(Error::unexpected(
            "GetItemID did not resolve the Boolean item",
        ));
    }

    let paths = unsafe { browse.BrowseAccessPaths(boolean_id) }
        .map_err(|error| Error::unexpected(format!("BrowseAccessPaths failed: {error}")))?;
    let mut no_path = windows_core::PWSTR::null();
    let mut no_path_count = 0;
    let no_path_status =
        unsafe { paths.Next(std::slice::from_mut(&mut no_path), Some(&mut no_path_count)) };
    if no_path_status.0 != 1 || no_path_count != 0 {
        return Err(Error::unexpected(
            "flat laboratory item unexpectedly returned access paths",
        ));
    }
    println!(
        "IOPCBrowseServerAddressSpace and IEnumString browse passed for both laboratory tags."
    );
    Ok(())
}

fn run_server(class_id: Guid, shutdown_flag: Option<std::path::PathBuf>) -> Result<()> {
    if shutdown_flag.as_ref().is_some_and(|path| path.exists()) {
        return Err(Error::invalid_argument(
            "refusing IPC test startup while its shutdown marker already exists",
        ));
    }
    let apartment = ComApartment::mta()?;
    let project_path = configured::project_path()?;
    if std::env::args().any(|arg| arg == "--project") && !project_path.exists() {
        return Err(Error::invalid_argument("the explicit --project file does not exist"));
    }
    let configured_service = if project_path.exists() {
        Some(configured::ConfiguredService::load(&project_path)?)
    } else { None };
    if is_source_lab_server() && configured_service.is_some() {
        return Err(Error::invalid_argument(
            "source-lab server must use its isolated folder without destination project.json",
        ));
    }
    let legacy_service = if configured_service.is_none() { Some(LabDaService::new()?) } else { None };
    let factory_service: Arc<dyn DaService> = match &configured_service {
        Some(service) => service.clone(),
        None => legacy_service.as_ref().expect("legacy source initialized").clone(),
    };
    let _diagnostics = configured_service.as_ref().map(|service| configured::start_diagnostics(service.clone())).transpose()?;
    let factory_configured = configured_service.clone();
    let factory = ClassFactory::new(move || {
        let service: Arc<dyn DaService> = match &factory_configured {
            Some(service) => service.client_instance(),
            None => factory_service.clone(),
        };
        Ok(DaServer::new(service).object())
    });
    let registration = LocalClassRegistration::register(&apartment, &class_id, factory)?;
    println!(
        "Volatile COM class object registered in process; no registry keys were written. CLSID={{{:08X}-{:04X}-{:04X}-{:02X}{:02X}-{:02X}{:02X}{:02X}{:02X}{:02X}{:02X}}}",
        class_id.data1,
        class_id.data2,
        class_id.data3,
        class_id.data4[0],
        class_id.data4[1],
        class_id.data4[2],
        class_id.data4[3],
        class_id.data4[4],
        class_id.data4[5],
        class_id.data4[6],
        class_id.data4[7]
    );
    println!("{REGISTRY_WARNING}");
    log_line(
        "START",
        &format!("volatile class factory ready clsid={:?}", class_id),
    );

    // A local CoGetClassObject probe verifies class-factory activation and the
    // IOPCServer interface without creating persistent ProgID/CLSID entries.
    let class_id = windows_core::GUID::from_values(
        class_id.data1,
        class_id.data2,
        class_id.data3,
        class_id.data4,
    );
    let factory: windows::Win32::System::Com::IClassFactory =
        unsafe { CoGetClassObject(&class_id, CLSCTX_LOCAL_SERVER, None) }
            .map_err(|error| Error::unexpected(format!("CoGetClassObject failed: {error}")))?;
    let server: windows_core::IUnknown = unsafe {
        factory.CreateInstance(None::<&windows_core::IUnknown>)
    }
    .map_err(|error| Error::unexpected(format!("IClassFactory activation failed: {error}")))?;
    let iopc_server_iid = windows_core::GUID::from_values(
        0x39c1_3a4d,
        0x011e,
        0x11d0,
        [0x96, 0x75, 0x00, 0x20, 0xaf, 0xd8, 0xad, 0xb3],
    );
    let mut da_interface = std::ptr::null_mut();
    let vtable =
        unsafe { *(Interface::as_raw(&server) as *const *const windows_core::IUnknown_Vtbl) };
    let query_hr = unsafe {
        ((*vtable).QueryInterface)(
            Interface::as_raw(&server),
            &iopc_server_iid,
            &mut da_interface,
        )
    };
    if query_hr.0 < 0 || da_interface.is_null() {
        return Err(Error::unexpected(format!(
            "IOPCServer QueryInterface failed: {:#010x}",
            query_hr.0 as u32
        )));
    }
    unsafe { windows_core::IUnknown::from_raw(da_interface) };
    println!("Class factory activation and IOPCServer QueryInterface passed.");
    let source_flag = std::env::var_os("RUST_DA_LAB_SOURCE_FLAG")
        .unwrap_or_else(|| if is_source_lab_server() {
            std::env::current_exe().ok()
                .and_then(|path| path.parent().map(|parent| parent.join("rust-da-source-offline.flag")))
                .unwrap_or_else(|| std::path::PathBuf::from("rust-da-source-offline.flag"))
                .into_os_string()
        } else if configured_service.is_some() {
            project_path.with_file_name("rust-da-source-offline.flag").into_os_string()
        } else { "rust-da-source-offline.flag".into() });
    let monitor_core = legacy_service.as_ref().map(|service| service.core.clone());
    let configured_source = configured_service.clone();
    let stop = Arc::new(AtomicBool::new(false));
    let thread_stop = stop.clone();
    let monitor = std::thread::spawn(move || {
        let mut source = LabSimulator::new();
        let mut was_available = true;
        while !thread_stop.load(Ordering::Relaxed) {
            let offline_flag = std::path::Path::new(&source_flag).exists();
            let override_result = if is_source_lab_server() && !offline_flag {
                read_source_lab_override()
            } else { Ok(None) };
            let available = !offline_flag && override_result.is_ok();
            if available != was_available {
                if let Err(error) = &override_result {
                    log_line("SOURCE_OVERRIDE_REJECTED", error);
                }
                if let Some(core) = &monitor_core {
                    if let Ok(mut core) = core.lock() { core.set_source_available(available); }
                }
                if let Some(service) = &configured_source {
                    if let Err(error) = service.set_source_available(available) { log_line("ERROR", &error.to_string()); }
                }
                was_available = available;
                log_line(
                    "SOURCE",
                    if available {
                        "available; next sample will restore quality"
                    } else {
                        "unavailable; cached quality degraded"
                    },
                );
            }
            if available {
                if let Ok(timestamp) = Timestamp::from_system_time(SystemTime::now()) {
                    if let Some(core) = &monitor_core {
                        if let Ok(mut core) = core.lock() {
                            let source_time = SourceTimestamp::ValidFileTime(timestamp.ticks());
                            let result = match override_result.as_ref().ok().and_then(|value| *value) {
                                Some((numeric, boolean)) => core.publish(TagId::NumericDouble, TagValue::Double(numeric), source_time)
                                    .and_then(|_| core.publish(TagId::Boolean, TagValue::Boolean(boolean), source_time)),
                                None => source.publish_next(&mut core, source_time),
                            };
                            if let Err(error) = result {
                                log_line("ERROR", &format!("simulated sample failed: {}", core_error(error)));
                            }
                        }
                    }
                    if let Some(service) = &configured_source {
                        if let Err(error) = service.tick(timestamp.ticks()) { log_line("ERROR", &error.to_string()); }
                    }
                }
            }
            std::thread::sleep(Duration::from_millis(250));
        }
    });
    let shutdown_flag = shutdown_flag.or_else(|| configured_service.as_ref().map(|_| project_path.with_file_name("agent-stop.flag")));
    match shutdown_flag {
        Some(path) => {
            log_line(
                "IPC_TEST",
                &format!("awaiting shutdown marker {}", path.display()),
            );
            while !path.exists() {
                std::thread::sleep(Duration::from_millis(250));
            }
            stop.store(true, Ordering::Relaxed);
            monitor
                .join()
                .map_err(|_| Error::unexpected("simulator thread panicked"))?;
            registration.revoke()?;
            log_line(
                "STOP",
                "IPC diagnostic server revoked and stopped by marker",
            );
            println!("IPC_TEST_SERVER_STOPPED");
            let _ = std::fs::remove_file(path);
        }
        None => loop {
            std::thread::sleep(Duration::from_secs(60));
        },
    }
    Ok(())
}

fn log_line(kind: &str, message: &str) {
    let line = format!(
        "{:?} pid={} [{kind}] {message}\n",
        SystemTime::now(),
        std::process::id()
    );
    let path = std::env::current_exe()
        .ok()
        .and_then(|executable| {
            executable
                .parent()
                .map(|parent| parent.join("rust-da-com-lab.log"))
        })
        .unwrap_or_else(|| std::path::PathBuf::from("rust-da-com-lab.log"));
    if let Ok(mut file) = OpenOptions::new().create(true).append(true).open(path) {
        let _ = file.write_all(line.as_bytes());
    }
    eprint!("{line}");
}

#[cfg(not(windows))]
fn main() {
    eprintln!("This COM server is Windows-only; use the independent model crate on other hosts.");
}
