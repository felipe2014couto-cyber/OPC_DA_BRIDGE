
fn load_or_create_auth_token(dir: &std::path::Path) -> Result<String> {
    let token_file = dir.join("runtime-auth.token");
    if let Some(token) = read_auth_token(&token_file)? {
        return Ok(token);
    }

    let mut buf = [0u8; 32];
    #[cfg(windows)]
    {
        #[link(name = "bcrypt")]
        extern "system" {
            fn BCryptGenRandom(
                h_algorithm: *mut std::ffi::c_void,
                pb_buffer: *mut u8,
                cb_buffer: u32,
                dw_flags: u32,
            ) -> i32;
        }
        let status = unsafe {
            BCryptGenRandom(
                std::ptr::null_mut(),
                buf.as_mut_ptr(),
                buf.len() as u32,
                0x00000002, // BCRYPT_USE_SYSTEM_PREFERRED_RNG
            )
        };
        if status != 0 {
            return Err(Error::unexpected(format!("BCryptGenRandom failed with status {status:#x}")));
        }
    }
    #[cfg(not(windows))]
    {
        let mut f = std::fs::File::open("/dev/urandom")
            .map_err(|e| Error::unexpected(format!("Failed to open /dev/urandom: {e}")))?;
        f.read_exact(&mut buf)
            .map_err(|e| Error::unexpected(format!("Failed to read random bytes: {e}")))?;
    }

    if buf.iter().all(|&b| b == 0) {
        return Err(Error::unexpected("Generated entropy token is all-zero"));
    }

    let token: String = buf.iter().map(|b| format!("{:02x}", b)).collect();
    match std::fs::OpenOptions::new().write(true).create_new(true).open(&token_file) {
        Ok(mut file) => {
            file.write_all(token.as_bytes())
                .and_then(|_| file.sync_all())
                .map_err(|e| Error::unexpected(format!("Failed to persist runtime-auth.token: {e}")))?;
            Ok(token)
        }
        Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {
            // Another COM server instance won the create race. Never replace its
            // token: the winner may already be serving requests with it in memory.
            for _ in 0..100 {
                if let Some(existing) = read_auth_token(&token_file)? {
                    return Ok(existing);
                }
                std::thread::sleep(std::time::Duration::from_millis(10));
            }
            Err(Error::unexpected("runtime-auth.token remained incomplete after concurrent creation"))
        }
        Err(error) => Err(Error::unexpected(format!("Failed to create runtime-auth.token: {error}"))),
    }
}

fn read_auth_token(path: &std::path::Path) -> Result<Option<String>> {
    let text = match std::fs::read_to_string(path) {
        Ok(value) => value,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(Error::unexpected(format!("Failed to read runtime-auth.token: {error}"))),
    };
    let token = text.trim();
    if token.is_empty() {
        // A concurrently-created file can be observed before the winning process
        // has completed its write. The creator path below waits for that write.
        return Ok(None);
    }
    if token.len() != 64 || !token.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        return Err(Error::unexpected("runtime-auth.token has an invalid format; refusing to replace it"));
    }
    Ok(Some(token.to_owned()))
}

/// Optional project-backed simulated source. The original two-tag path is untouched.
use super::*;
use std::time::{SystemTime, UNIX_EPOCH};
use opc_da_project::{DataType, Project, ProjectRuntime, Sample, SimValue};
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::path::PathBuf;
use std::sync::atomic::AtomicU32;

fn hresult(code: u32) -> ErrorCode {
    ErrorCode::from_raw(code as i32)
}
fn lock_error() -> Error {
    Error::unexpected("configured laboratory state poisoned")
}
fn kind(t: DataType) -> ValueType {
    match t {
        DataType::Boolean => ValueType::BOOL,
        DataType::Double => ValueType::F64,
    }
}

pub fn project_path() -> Result<PathBuf> {
    let args: Vec<_> = std::env::args_os().collect();
    if let Some(index) = args.iter().position(|a| a == "--project") {
        return args
            .get(index + 1)
            .map(PathBuf::from)
            .ok_or_else(|| Error::invalid_argument("--project requires a path"));
    }
    Ok(std::env::current_exe()
        .map_err(|e| Error::unexpected(e.to_string()))?
        .with_file_name("project.json"))
}

pub struct ConfiguredService {
    pub runtime: Arc<Mutex<ProjectRuntime>>,
    pub auth_token: Arc<String>,
    start: Timestamp,
    groups: Mutex<HashMap<u32, Arc<ConfiguredGroup>>>,
}
impl ConfiguredService {
    pub fn client_instance(&self) -> Arc<Self> {
        // Each COM server object owns its groups; all objects share the same cache.
        Arc::new(Self {
            runtime: self.runtime.clone(),
            auth_token: self.auth_token.clone(),
            start: self.start,
            groups: Mutex::new(HashMap::new()),
        })
    }
    pub fn load(path: &std::path::Path) -> Result<Arc<Self>> {
        let bytes = std::fs::read(path).map_err(|e| Error::invalid_argument(e.to_string()))?;
        if bytes.len() as u64 > opc_da_project::MAX_CONFIG_BYTES { return Err(Error::invalid_argument("project too large")); }
        let project: Project = serde_json::from_slice(&bytes).map_err(|e| Error::invalid_argument(e.to_string()))?;
        let mut runtime = ProjectRuntime::new(project).map_err(Error::invalid_argument)?;
        runtime.config_sha256 = Some(opc_da_project::sha256(&bytes).map_err(Error::unexpected)?);
        runtime.loaded_deployment = runtime.project.deployment.clone();
        let start = Timestamp::from_system_time(SystemTime::now())?;
        runtime.tick(Some(start.ticks()));
        log_line(
            "PROJECT_LOADED",
            &format!(
                "path={} revision={} variables={} restart_required_to_apply=true",
                path.display(),
                runtime.project.revision(),
                runtime.snapshot().samples.len()
            ),
        );
        let dir = path.parent().unwrap_or(std::path::Path::new("."));
        let auth_token = load_or_create_auth_token(dir)?;
        Ok(Arc::new(Self {
            runtime: Arc::new(Mutex::new(runtime)),
            auth_token: Arc::new(auth_token),
            start,
            groups: Mutex::new(HashMap::new()),
        }))
    }
    pub fn tick(&self, timestamp: u64) -> Result<()> {
        self.runtime
            .lock()
            .map_err(|_| lock_error())?
            .tick(Some(timestamp));
        Ok(())
    }
    pub fn set_source_available(&self, available: bool) -> Result<()> {
        self.runtime
            .lock()
            .map_err(|_| lock_error())?
            .set_source_available(available);
        Ok(())
    }
}
impl DaService for ConfiguredService {
    fn browse_hierarchical(&self) -> bool { true }

    fn browse_items(&self) -> Vec<BrowseItem> {
        match self.runtime.lock() {
            Ok(runtime) => runtime
                .project
                .published_variables()
                .into_iter()
                .map(|v| BrowseItem {
                    item_id: v.item_id,
                    data_type: match v.variable.data_type {
                        DataType::Boolean => 11,
                        DataType::Double => 5,
                    },
                    access_rights: 1,
                })
                .collect(),
            Err(_) => {
                log_line("ERROR", "configured browse lock poisoned");
                Vec::new()
            }
        }
    }
    fn status(&self) -> Result<ServerStatus> {
        let snapshot = self.runtime.lock().map_err(|_| lock_error())?.snapshot();
        Ok(ServerStatus {
            start_time: self.start,
            current_time: Timestamp::from_system_time(SystemTime::now())?,
            last_update_time: Timestamp::from_ticks(
                snapshot
                    .samples
                    .iter()
                    .filter_map(|s| s.source_timestamp_filetime)
                    .max()
                    .unwrap_or(self.start.ticks()),
            ),
            state: ServerState::Running,
            group_count: self.groups.lock().map_err(|_| lock_error())?.len() as u32,
            bandwidth: 0,
            version: (0, 2, 0),
            vendor_info: "Rust OPC DA Lab — configuração local, fonte simulada".into(),
        })
    }
    fn add_group(
        &self,
        server_handle: u32,
        options: GroupOptions,
    ) -> Result<Arc<dyn DaGroupService>> {
        let mut groups = self.groups.lock().map_err(|_| lock_error())?;
        if groups.values().any(|g| g.name == options.name) {
            return Err(Error::from_code(hresult(0xc004000c)));
        }
        let group = Arc::new(ConfiguredGroup {
            name: options.name.clone(),
            runtime: self.runtime.clone(),
            items: Mutex::new(GroupItems {
                removed: false,
                items: HashMap::new(),
            }),
            next_handle: AtomicU32::new(1),
        });
        groups.insert(server_handle, group.clone());
        log_line(
            "ADD_GROUP",
            &format!(
                "configured=true server_handle={server_handle} name={:?}",
                options.name
            ),
        );
        Ok(group)
    }
    fn remove_group(&self, server_handle: u32, _force: bool) -> Result<()> {
        let group = self
            .groups
            .lock()
            .map_err(|_| lock_error())?
            .remove(&server_handle)
            .ok_or_else(|| Error::invalid_argument("unknown group"))?;
        group.items.lock().map_err(|_| lock_error())?.removed = true;
        log_line(
            "REMOVE_GROUP",
            &format!("configured=true server_handle={server_handle}"),
        );
        Ok(())
    }
    fn error_string(&self, error: ErrorCode, _locale: u32) -> Result<String> {
        Ok(format!("Rust DA lab {error}"))
    }
}
struct GroupItem {
    item_id: String,
    client: u32,
    active: bool,
    data_type: ValueType,
}
struct GroupItems {
    removed: bool,
    items: HashMap<u32, GroupItem>,
}
struct ConfiguredGroup {
    name: String,
    runtime: Arc<Mutex<ProjectRuntime>>,
    items: Mutex<GroupItems>,
    next_handle: AtomicU32,
}
impl ConfiguredGroup {
    fn change(
        &self,
        handles: &[ServerItemHandle],
        mut f: impl FnMut(&mut GroupItem),
    ) -> Result<Vec<ServerItemResult<()>>> {
        let mut group = self.items.lock().map_err(|_| lock_error())?;
        if group.removed {
            return Err(Error::invalid_argument("group removed"));
        }
        Ok(handles
            .iter()
            .map(|h| match group.items.get_mut(&h.raw()) {
                Some(i) => {
                    f(i);
                    ServerItemResult::success(())
                }
                None => ServerItemResult::failure(hresult(0xc0040001)),
            })
            .collect())
    }
}
impl DaGroupService for ConfiguredGroup {
    fn diagnostic(&self, event: &str) {
        log_line("COM_SYNCIO", event);
    }
    fn validate_read_handles(&self, handles: &[ServerItemHandle]) -> Result<Vec<ServerItemResult<ClientItemHandle>>> {
        let group = self.items.lock().map_err(|_| lock_error())?;
        if group.removed { return Err(Error::invalid_argument("group removed")); }
        Ok(handles.iter().map(|h| match group.items.get(&h.raw()) {
            Some(item) => ServerItemResult::success(ClientItemHandle(item.client)),
            None => ServerItemResult::failure(hresult(0xc0040001)),
        }).collect())
    }
    fn active_refresh_items(&self) -> Result<Vec<(ServerItemHandle, ClientItemHandle)>> {
        let group = self.items.lock().map_err(|_| lock_error())?;
        if group.removed {
            return Err(Error::invalid_argument("group removed"));
        }
        let mut items: Vec<_> = group
            .items
            .iter()
            .filter(|(_, i)| i.active)
            .map(|(h, i)| (ServerItemHandle::from_raw(*h), ClientItemHandle(i.client)))
            .collect();
        items.sort_by_key(|(h, _)| h.raw());
        Ok(items)
    }
    fn add_items(&self, items: &[ItemSpec]) -> Result<Vec<ServerItemResult<ServerAddedItem>>> {
        let mut group = self.items.lock().map_err(|_| lock_error())?;
        if group.removed {
            return Err(Error::invalid_argument("group removed"));
        }
        let runtime = self.runtime.lock().map_err(|_| lock_error())?;
        Ok(items
            .iter()
            .map(|item| {
                let result = (|| {
                    let sample = runtime.sample(&item.item_id).ok_or(hresult(0xc0040007))?;
                    let data_type = kind(sample.data_type);
                    if item.requested_data_type != ValueType::EMPTY
                        && item.requested_data_type != data_type
                    {
                        return Err(hresult(0xc0040004));
                    }
                    let handle = self
                        .next_handle
                        .fetch_update(Ordering::Relaxed, Ordering::Relaxed, |v| v.checked_add(1))
                        .map_err(|_| ErrorCode::UNEXPECTED)?;
                    group.items.insert(
                        handle,
                        GroupItem {
                            item_id: item.item_id.clone(),
                            client: item.client_handle.0,
                            active: item.active,
                            data_type,
                        },
                    );
                    Ok(ServerAddedItem {
                        server_handle: ServerItemHandle::from_raw(handle),
                        canonical_data_type: data_type,
                        access_rights: 1,
                        blob: Vec::new(),
                    })
                })();
                match &result {
                    Ok(added) => log_line(
                        "ADD_ITEM_RESULT",
                        &format!(
                            "item_id={:?} server_handle={} canonical_type={:?} result=S_OK",
                            item.item_id,
                            added.server_handle.raw(),
                            added.canonical_data_type
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
                };
                result.map_or_else(ServerItemResult::failure, ServerItemResult::success)
            })
            .collect())
    }
    fn remove_items(&self, handles: &[ServerItemHandle]) -> Result<Vec<ServerItemResult<()>>> {
        let mut group = self.items.lock().map_err(|_| lock_error())?;
        Ok(handles
            .iter()
            .map(|h| {
                if group.items.remove(&h.raw()).is_some() {
                    ServerItemResult::success(())
                } else {
                    ServerItemResult::failure(hresult(0xc0040001))
                }
            })
            .collect())
    }
    fn set_active(
        &self,
        handles: &[ServerItemHandle],
        active: bool,
    ) -> Result<Vec<ServerItemResult<()>>> {
        self.change(handles, |i| i.active = active)
    }
    fn set_client_handles(
        &self,
        handles: &[(ServerItemHandle, ClientItemHandle)],
    ) -> Result<Vec<ServerItemResult<()>>> {
        let mut group = self.items.lock().map_err(|_| lock_error())?;
        Ok(handles
            .iter()
            .map(|(h, c)| match group.items.get_mut(&h.raw()) {
                Some(i) => {
                    i.client = c.0;
                    ServerItemResult::success(())
                }
                None => ServerItemResult::failure(hresult(0xc0040001)),
            })
            .collect())
    }
    fn set_data_types(
        &self,
        handles: &[(ServerItemHandle, ValueType)],
    ) -> Result<Vec<ServerItemResult<()>>> {
        let group = self.items.lock().map_err(|_| lock_error())?;
        Ok(handles
            .iter()
            .map(|(h, t)| match group.items.get(&h.raw()) {
                Some(i) if *t == ValueType::EMPTY || *t == i.data_type => {
                    ServerItemResult::success(())
                }
                Some(_) => ServerItemResult::failure(hresult(0xc0040004)),
                None => ServerItemResult::failure(hresult(0xc0040001)),
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
            &format!("configured=true source={source:?} count={}", handles.len()),
        );
        let group = self.items.lock().map_err(|_| lock_error())?;
        if group.removed {
            return Err(Error::invalid_argument("group removed"));
        }
        let runtime = self.runtime.lock().map_err(|_| lock_error())?;
        Ok(handles.iter().map(|h| {
            let result=(|| {let item=group.items.get(&h.raw()).ok_or(hresult(0xc0040001))?;let sample=runtime.sample(&item.item_id).ok_or(hresult(0xc0040007))?;sample_to_com(sample,item.client)})();
            match &result {Ok(s)=>log_line("READ_RESULT",&format!("server_handle={} client_handle={} value={:?} quality={:#04x} filetime={} result=S_OK",h.raw(),s.client_handle.0,s.value,s.quality,s.timestamp.ticks())),Err(e)=>log_line("READ_RESULT",&format!("server_handle={} hresult={:#010x}",h.raw(),e.raw() as u32))};
            result.map_or_else(ServerItemResult::failure,ServerItemResult::success)
        }).collect())
    }
    fn write(&self, values: &[(ServerItemHandle, Value)]) -> Result<Vec<ServerItemResult<()>>> {
        log_line(
            "WRITE_REJECTED",
            &format!("configured=true count={} hresult=0xc0040006", values.len()),
        );
        Ok(values
            .iter()
            .map(|_| ServerItemResult::failure(hresult(0xc0040006)))
            .collect())
    }
}
fn sample_to_com(sample: Sample, client: u32) -> std::result::Result<ServerSample, ErrorCode> {
    if sample.hresult < 0 { return Err(hresult(sample.hresult as u32)); }
    // DA 2 has no nullable successful FILETIME. A zero success timestamp makes
    // PI Client's callback throw RPC_E_SERVERFAULT. Keep the cache's missing
    // timestamp explicit and return a per-item failure across the COM boundary.
    let timestamp = sample.source_timestamp_filetime.filter(|t| *t > 0 && *t <= 2650467743999999999)
        .ok_or_else(|| hresult(0x80004005))?;
    let value = match sample.value {
        Some(SimValue::Double(v)) => Value::F64(v),
        Some(SimValue::Boolean(v)) => Value::Bool(v),
        None => return Err(hresult(0xc0040008)),
    };
    Ok(ServerSample {
        client_handle: ClientItemHandle(client),
        timestamp: Timestamp::from_ticks(timestamp),
        quality: sample.quality,
        value,
    })
}

pub struct Diagnostics {
    stop: Arc<AtomicBool>,
    thread: Option<std::thread::JoinHandle<()>>,
}
impl Drop for Diagnostics {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Relaxed);
        if let Some(t) = self.thread.take() {
            let _ = t.join();
        }
    }
}
pub fn start_diagnostics(service: Arc<ConfiguredService>) -> Result<Diagnostics> {
    let port = service
        .runtime
        .lock()
        .map_err(|_| lock_error())?
        .project
        .diagnostic_port;
    let listener = TcpListener::bind(std::net::SocketAddr::from(([127, 0, 0, 1], port)))
        .map_err(|e| Error::unexpected(format!("loopback diagnostics port {port}: {e}")))?;
    listener
        .set_nonblocking(true)
        .map_err(|e| Error::unexpected(e.to_string()))?;
    let stop = Arc::new(AtomicBool::new(false));
    let worker_stop = stop.clone();
    let thread = std::thread::spawn(move || {
        while !worker_stop.load(Ordering::Relaxed) {
            match listener.accept() {
                Ok((mut stream, _)) => {
                    if let Err(e) = respond(&mut stream, &service) {
                        log_line("DIAGNOSTIC_ERROR", &e.to_string());
                    }
                }
                Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                    std::thread::sleep(Duration::from_millis(50))
                }
                Err(e) => {
                    log_line("DIAGNOSTIC_ERROR", &e.to_string());
                    break;
                }
            }
        }
    });
    log_line(
        "DIAGNOSTICS",
        &format!("read_only=true bind=127.0.0.1:{port} path=/v1/snapshot"),
    );
    Ok(Diagnostics {
        stop,
        thread: Some(thread),
    })
}
fn respond(stream: &mut TcpStream, service: &ConfiguredService) -> std::io::Result<()> {
    // The listener is nonblocking so accept() can be polled for shutdown. On
    // Windows, accepted sockets may retain that mode; HTTP bodies can arrive
    // after their headers, so keep each accepted client stream blocking.
    stream.set_nonblocking(false)?;
    stream.set_read_timeout(Some(Duration::from_millis(1500)))?;
    stream.set_write_timeout(Some(Duration::from_millis(1500)))?;
    let mut request = Vec::new();
    let mut block = [0u8; 512];
    let header_end = loop {
        if let Some(pos) = request.windows(4).position(|w| w == b"\r\n\r\n") {
            break pos + 4;
        }
        let n = stream.read(&mut block)?;
        if n == 0 {
            break 0;
        }
        request.extend_from_slice(&block[..n]);
        if request.len() > 16384 {
            break 0;
        }
    };
    if header_end == 0 {
        return Ok(());
    }
    let header_str = String::from_utf8_lossy(&request[..header_end]);
    let first_line = header_str.lines().next().unwrap_or("");
    let is_get_snapshot = first_line.starts_with("GET /v1/snapshot HTTP/1.");
    let is_post_update = first_line.starts_with("POST /v1/update HTTP/1.");
    let is_source_session = first_line.starts_with("POST /v1/source/session HTTP/1.");
    let is_source_samples = first_line.starts_with("POST /v1/source/samples HTTP/1.");

    let (status, body) = if is_get_snapshot {
        match service.runtime.lock() {
            Ok(runtime) => (
                "200 OK",
                serde_json::to_vec(&runtime.snapshot()).map_err(std::io::Error::other)?,
            ),
            Err(_) => ("500 Internal Server Error", b"{\"ok\":false,\"error\":\"state unavailable\"}".to_vec()),
        }
    } else if is_post_update || is_source_session || is_source_samples {
        let mut authorized = false;
        let expected_token = service.auth_token.as_str();
        for line in header_str.lines() {
            let lower = line.to_ascii_lowercase();
            if lower.starts_with("authorization:") {
                let parts: Vec<&str> = line[14..].trim().split_whitespace().collect();
                if parts.len() == 2 && parts[0].eq_ignore_ascii_case("bearer") && parts[1] == expected_token {
                    authorized = true;
                    break;
                }
            }
        }
        if !authorized {
            log_line("AUTH_FAILED", "unauthorized attempt to access /v1/update");
            ("401 Unauthorized", b"{\"ok\":false,\"error\":\"Acesso local n\xc3\xa3o autorizado.\"}".to_vec())
        } else {
            let content_length = header_str.lines()
                .find(|l| l.to_ascii_lowercase().starts_with("content-length:"))
                .and_then(|l| l[15..].trim().parse::<usize>().ok())
                .unwrap_or(0);
            if content_length == 0 {
                ("400 Bad Request", b"{\"ok\":false,\"error\":\"Content-Length obrigat\xc3\xb3rio.\"}".to_vec())
            } else if content_length > opc_da_project::MAX_CONFIG_BYTES as usize {
                ("413 Payload Too Large", b"{\"ok\":false,\"error\":\"Payload excede o limite.\"}".to_vec())
            } else {
                let mut body_bytes = request[header_end..].to_vec();
                let mut incomplete = false;
                while body_bytes.len() < content_length {
                    let n = stream.read(&mut block)?;
                    if n == 0 {
                        incomplete = true;
                        break;
                    }
                    body_bytes.extend_from_slice(&block[..n]);
                }
                if incomplete || body_bytes.len() < content_length {
                    ("400 Bad Request", b"{\"ok\":false,\"error\":\"Corpo da requisi\xc3\xa7\xc3\xa3o incompleto.\"}".to_vec())
                } else {
                    body_bytes.truncate(content_length);

                    let result = if is_post_update { handle_hot_update(service, &body_bytes) }
                        else { handle_source_update(service, &body_bytes, is_source_session) };
                    match result {
                        Ok(resp_bytes) => ("200 OK", resp_bytes),
                        Err((err_status, err_msg)) => {
                            let err_json = serde_json::json!({
                                "ok": false,
                                "error": err_msg
                            });
                            (err_status, serde_json::to_vec(&err_json).unwrap_or_default())
                        }
                    }
                }
            }
        }
    } else {
        (
            "405 Method Not Allowed",
            b"{\"ok\":false,\"error\":\"read-only loopback laboratory endpoint\"}".to_vec(),
        )
    };
    write!(stream,"HTTP/1.0 {status}\r\nContent-Type: application/json; charset=utf-8\r\nCache-Control: no-store\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",body.len())?;
    stream.write_all(&body)?;
    Ok(())
}

fn handle_source_update(service: &ConfiguredService, body: &[u8], session: bool) -> std::result::Result<Vec<u8>, (&'static str, String)> {
    let payload: serde_json::Value = serde_json::from_slice(body).map_err(|_| ("400 Bad Request", "JSON inválido".into()))?;
    let text = |key: &str| payload.get(key).and_then(|v| v.as_str()).ok_or(("400 Bad Request", format!("Campo ausente: {key}")));
    let number = |key: &str| payload.get(key).and_then(|v| v.as_u64()).ok_or(("400 Bad Request", format!("Campo ausente: {key}")));
    let module = text("source_module_id")?;
    let mut runtime = service.runtime.lock().map_err(|_| ("500 Internal Server Error", "Runtime indisponível".into()))?;
    runtime.check_source_target(text("machine_id")?, number("configuration_revision")?, text("config_sha256")?, module)
        .map_err(|e| ("409 Conflict", e))?;
    let mut reply = if session {
        let id = runtime.start_source_session(module);
        log_line("SOURCE_SESSION", &format!("module={module} session={id}"));
        serde_json::json!({"ok":true,"session_id":id,"pid":std::process::id()})
    } else {
        let samples: Vec<opc_da_project::SourceSample> = serde_json::from_value(payload.get("samples").cloned().unwrap_or_default())
            .map_err(|_| ("400 Bad Request", "Amostras inválidas".into()))?;
        let sequence = number("sequence")?;
        runtime.apply_source_samples(module, number("session_id")?, sequence, samples)
            .map_err(|e| ("409 Conflict", e))?;
        log_line("SOURCE_SAMPLES_ACK", &format!("module={module} sequence={sequence} pid={}", std::process::id()));
        serde_json::json!({"ok":true,"sequence":sequence,"pid":std::process::id(),"all_good":runtime.snapshot().samples.iter().filter(|s| s.source_module_id.as_deref()==Some(module)).all(|s| s.quality&0xc0==0xc0 && s.hresult>=0)})
    };
    reply["machine_id"] = payload["machine_id"].clone();
    reply["configuration_revision"] = payload["configuration_revision"].clone();
    reply["config_sha256"] = payload["config_sha256"].clone();
    reply["source_module_id"] = payload["source_module_id"].clone();
    serde_json::to_vec(&reply).map_err(|e| ("500 Internal Server Error", e.to_string()))
}

fn handle_hot_update(service: &ConfiguredService, body: &[u8]) -> std::result::Result<Vec<u8>, (&'static str, String)> {
    let payload: serde_json::Value = serde_json::from_slice(body)
        .map_err(|e| ("400 Bad Request", format!("JSON inv??lido: {e}")))?;

    let project_sha256 = payload.get("project_sha256").and_then(|v| v.as_str()).map(|s| s.to_string());
    let timestamp = payload.get("timestamp").and_then(|v| v.as_u64());

    let (project, digest, project_bytes) = if let Some(pj) = payload.get("project_json").and_then(|v| v.as_str()) {
        #[cfg(windows)]
        let computed = opc_da_project::sha256(pj.as_bytes())
            .map_err(|e| ("500 Internal Server Error", format!("Falha ao calcular SHA-256: {e}")))?;
        #[cfg(not(windows))]
        let computed = project_sha256.clone().unwrap_or_default();

        if let Some(ref supplied) = project_sha256 {
            if !supplied.is_empty() && !computed.is_empty() && supplied != &computed {
                return Err(("400 Bad Request", "Digest SHA-256 informado n??o confere com o conte??do.".into()));
            }
        }
        let p: Project = serde_json::from_str(pj)
            .map_err(|e| ("400 Bad Request", format!("Projeto inv??lido: {e}")))?;
        let bytes = pj.as_bytes().to_vec();
        let digest_to_use = if !computed.is_empty() { computed } else { project_sha256.unwrap_or_default() };
        (p, digest_to_use, bytes)
    } else if let Some(p_val) = payload.get("project") {
        let p: Project = serde_json::from_value(p_val.clone())
            .map_err(|e| ("400 Bad Request", format!("Projeto inv??lido: {e}")))?;
        let serialized = serde_json::to_vec_pretty(&p)
            .map_err(|e| ("400 Bad Request", format!("Falha ao serializar projeto: {e}")))?;
        #[cfg(windows)]
        let computed = opc_da_project::sha256(&serialized)
            .map_err(|e| ("500 Internal Server Error", format!("Falha ao calcular SHA-256: {e}")))?;
        #[cfg(not(windows))]
        let computed = project_sha256.clone().unwrap_or_default();

        if let Some(ref supplied) = project_sha256 {
            if !supplied.is_empty() && !computed.is_empty() && supplied != &computed {
                return Err(("400 Bad Request", "Digest SHA-256 informado n??o confere com o conte??do.".into()));
            }
        }
        let digest_to_use = if !computed.is_empty() { computed } else { project_sha256.unwrap_or_default() };
        (p, digest_to_use, serialized)
    } else {
        return Err(("400 Bad Request", "Informe project_json ou project.".into()));
    };

    let ts = timestamp.or_else(|| {
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .ok()
            .map(|d| d.as_secs() * 10_000_000 + (d.subsec_nanos() as u64 / 100) + 116444736000000000)
    });

    // 1. Acquire mutex on live runtime
    let mut runtime = service.runtime.lock().map_err(|_| ("500 Internal Server Error", "Estado do servidor inacess??vel.".into()))?;

    // 2. Clone runtime under mutex to validate candidate in isolation
    let mut candidate = runtime.clone();

    // 3. Apply revision to candidate runtime (validates structural equality, revision conflict, types, values)
    candidate.apply_value_revision(project, digest.clone(), ts)
        .map_err(|e| ("400 Bad Request", e))?;

    // 4. Atomically persist project.json to disk BEFORE replacing live runtime
    let path = project_path()
        .map_err(|e| ("500 Internal Server Error", format!("Falha ao resolver caminho do projeto: {e}")))?;
    let temp = path.with_extension(format!("json.tmp-{}", std::process::id()));
    let write_res = (|| -> std::io::Result<()> {
        let mut file = std::fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&temp)?;
        file.write_all(&project_bytes)?;
        file.sync_all()?;
        drop(file);
        opc_da_project::replace_file(&temp, &path)
    })();
    if let Err(e) = write_res {
        let _ = std::fs::remove_file(&temp);
        return Err(("500 Internal Server Error", format!("Falha ao persistir projeto atomicamente em disco: {e}")));
    }

    // 5. Swap live runtime: assign validated candidate
    *runtime = candidate;

    // 6. Generate snapshot and ACK from live runtime
    let snap = runtime.snapshot();
    let receipt = serde_json::json!({
        "machine_id": snap.machine_id,
        "configuration_revision": snap.configuration_revision,
        "project_sha256": snap.config_sha256,
        "pid": std::process::id()
    });

    log_line("HOT_UPDATE_ACK", &format!("revision={:?} digest={:?} pid={}", snap.configuration_revision, snap.config_sha256, std::process::id()));

    let ack = serde_json::json!({
        "ok": true,
        "receipt": receipt,
        "snapshot": snap
    });

    serde_json::to_vec(&ack).map_err(|e| ("500 Internal Server Error", e.to_string()))
}

/// Walk the actual COM branch/leaf interfaces, resolving leaf names to stable IDs.
fn hierarchy_names(browse: &DaBrowseServerAddressSpace, kind: opc_da_bindings::DaBrowseType) -> Result<Vec<String>> {
    let enumerator = unsafe { browse.BrowseOPCItemIDs(kind, windows_core::w!(""), 0, DA_ACCESS_READABLE) }
        .map_err(|e| Error::unexpected(e.to_string()))?;
    let mut values = Vec::new();
    for _ in 0..=10000 {
        let mut name = windows_core::PWSTR::null(); let mut fetched=0;
        let hr=unsafe { enumerator.Next(std::slice::from_mut(&mut name), Some(&mut fetched)) };
        if hr.0<0 { return Err(Error::unexpected(format!("hierarchy Next HRESULT={:#x}",hr.0))); }
        if fetched==0 { return Ok(values); }
        if name.is_null() { return Err(Error::unexpected("hierarchy enumerator returned null string")); }
        let text=unsafe { name.to_string() }.map_err(|e|Error::unexpected(e.to_string()));
        unsafe { CoTaskMemFree(Some(name.0.cast())) };values.push(text?);
    }
    Err(Error::unexpected("hierarchy enumeration did not terminate"))
}

fn hierarchy_change(browse: &DaBrowseServerAddressSpace, direction: opc_da_bindings::DaBrowseDirection, path: &str) -> Result<()> {
    let wide:Vec<u16>=path.encode_utf16().chain(std::iter::once(0)).collect();
    unsafe { browse.ChangeBrowsePosition(direction, windows_core::PCWSTR(wide.as_ptr())) }
        .map_err(|e|Error::unexpected(e.to_string()))
}

fn hierarchy_walk(browse: &DaBrowseServerAddressSpace, found: &mut Vec<String>, depth: usize) -> Result<()> {
    if depth>64 || found.len()>10000 { return Err(Error::unexpected("hierarchy safety bound exceeded")); }
    for name in hierarchy_names(browse,opc_da_bindings::DA_BROWSE_LEAF)? {
        let wide:Vec<u16>=name.encode_utf16().chain(std::iter::once(0)).collect();
        let resolved=unsafe { browse.GetItemID(windows_core::PCWSTR(wide.as_ptr())) }.map_err(|e|Error::unexpected(e.to_string()))?;
        let text=unsafe { resolved.to_string() }.map_err(|e|Error::unexpected(e.to_string()));
        unsafe { CoTaskMemFree(Some(resolved.0.cast())) };found.push(text?);
    }
    for branch in hierarchy_names(browse,opc_da_bindings::DA_BROWSE_BRANCH)? {
        println!("PROJECT_HIERARCHY_BRANCH depth={depth} name={branch}");
        hierarchy_change(browse,opc_da_bindings::DA_BROWSE_DOWN,&branch)?;
        hierarchy_walk(browse,found,depth+1)?;
        hierarchy_change(browse,DA_BROWSE_UP,"")?;
    }
    Ok(())
}

/// Diagnostic client: local laboratory CLSID only; --read-only-project-client suppresses writes.
pub fn client_test() -> Result<()> {
    let project = Project::load(&project_path()?).map_err(Error::invalid_argument)?;
    let snapshot =
        opc_da_project::fetch_snapshot(project.diagnostic_port).map_err(Error::unexpected)?;
    if snapshot.pid == std::process::id() || snapshot.config_revision != project.revision() {
        return Err(Error::unexpected("server PID/revision mismatch"));
    }
    let apartment = ComApartment::mta()?;
    let client = DaClient::connect_class_id(
        &apartment,
        &runtime_clsid(),
        opc_classic_types::ClassContext::LOCAL_SERVER,
    )?;
    let object = client.object();
    let queried = object.query_interface(&Guid::new(
        0x39c13a4f,
        0x011e,
        0x11d0,
        [0x96, 0x75, 0, 0x20, 0xaf, 0xd8, 0xad, 0xb3],
    ))?;
    let browse = unsafe { DaBrowseServerAddressSpace::from_raw(queried.into_raw()) };
    if unsafe { browse.QueryOrganization() }.map_err(|e|Error::unexpected(e.to_string()))?.0 == 1 {
        let mut found=Vec::new();hierarchy_walk(&browse,&mut found,0)?;found.sort();
        let mut expected:Vec<_>=project.published_variables().into_iter().map(|v|v.item_id).collect();expected.sort();
        if found!=expected {return Err(Error::unexpected(format!("hierarchy leaf mismatch {found:?}")));}
        if unsafe { browse.ChangeBrowsePosition(DA_BROWSE_UP,windows_core::w!("")) }.as_ref().err().is_none_or(|e|e.code().0!=0x80004005u32 as i32) {
            return Err(Error::unexpected("hierarchy UP at root did not terminate with E_FAIL"));
        }
        // A second client must remain at root while the first moves downward.
        if let Some(first)=hierarchy_names(&browse,opc_da_bindings::DA_BROWSE_BRANCH)?.first() {
            hierarchy_change(&browse,opc_da_bindings::DA_BROWSE_DOWN,first)?;
            let other=DaClient::connect_class_id(&apartment,&runtime_clsid(),opc_classic_types::ClassContext::LOCAL_SERVER)?;
            let raw=other.object().query_interface(&Guid::new(0x39c13a4f,0x011e,0x11d0,[0x96,0x75,0,0x20,0xaf,0xd8,0xad,0xb3]))?;
            let other_browse=unsafe { DaBrowseServerAddressSpace::from_raw(raw.into_raw()) };
            let root=unsafe { other_browse.GetItemID(windows_core::w!("")) }.map_err(|e|Error::unexpected(e.to_string()))?;
            let root_text=unsafe { root.to_string() }.map_err(|e|Error::unexpected(e.to_string()));
            unsafe { CoTaskMemFree(Some(root.0.cast())) };
            if !root_text?.is_empty() {return Err(Error::unexpected("browse position leaked between clients"));}
            hierarchy_change(&browse,opc_da_bindings::DA_BROWSE_TO,"")?;
        }
        println!("PROJECT_HIERARCHY_PASS leaf_ids={found:?} root_up=E_FAIL independent_clients=true");
    }
    let enumerator = unsafe {
        browse.BrowseOPCItemIDs(DA_BROWSE_FLAT, windows_core::w!(""), 0, DA_ACCESS_READABLE)
    }
    .map_err(|e| Error::unexpected(e.to_string()))?;
    let mut browsed = Vec::new();
    for _ in 0..=10_000 {
        let mut id = windows_core::PWSTR::null();
        let mut fetched = 0;
        let hr = unsafe { enumerator.Next(std::slice::from_mut(&mut id), Some(&mut fetched)) };
        if hr.0 < 0 {
            return Err(Error::unexpected(format!("browse HRESULT={:#x}", hr.0)));
        }
        if fetched == 0 {
            break;
        }
        let value = unsafe { id.to_string() }.map_err(|e| Error::unexpected(e.to_string()));
        unsafe { CoTaskMemFree(Some(id.0.cast())) };
        browsed.push(value?);
    }
    let published = project.published_variables();
    let mut expected: Vec<_> = published.iter().map(|v| v.item_id.clone()).collect();
    expected.sort();
    browsed.sort();
    if browsed != expected {
        return Err(Error::unexpected(format!(
            "browse mismatch actual={browsed:?} expected={expected:?}"
        )));
    }
    println!("PROJECT_BROWSE_PASS items={browsed:?}");
    let group = client.add_group(GroupOptions::new(format!(
        "ConfiguredProcessTest{}",
        std::process::id()
    )))?;
    let specs: Vec<_> = published
        .iter()
        .enumerate()
        .map(|(i, v)| ItemSpec::new(v.item_id.clone(), i as u32 + 300))
        .collect();
    let results = group.add_items(&specs)?;
    let mut handles = Vec::new();
    for (v, result) in published.iter().zip(results) {
        let added =
            result.map_err(|e| Error::unexpected(format!("AddItems {}: {e}", v.item_id)))?;
        if added.canonical_data_type != kind(v.variable.data_type) || added.access_rights != 1 {
            return Err(Error::unexpected("wrong canonical type or access rights"));
        }
        handles.push(added.server_handle);
    }
    for source in [DataSource::Cache, DataSource::Device] {
        if source == DataSource::Device {
            let args: Vec<_> = std::env::args().collect();
            if let Some(i) = args.iter().position(|a| a == "--project-client-pause-ms") {
                let delay: u64 = args.get(i+1).and_then(|a| a.parse().ok()).filter(|n| *n<=60000).ok_or_else(||Error::invalid_argument("invalid client pause"))?;
                std::thread::sleep(Duration::from_millis(delay));
            }
        }
        let current = opc_da_project::fetch_snapshot(project.diagnostic_port).map_err(Error::unexpected)?;
        let results = group.read(source, &handles)?;
        for (v, result) in published.iter().zip(results) {
            let sample =
                result.map_err(|e| Error::unexpected(format!("read {}: {e}", v.item_id)))?;
            let expected_value = if v.variable.source.is_some() {
                current.samples.iter().find(|s| s.item_id==v.item_id).and_then(|s|s.value.clone()).ok_or_else(||Error::unexpected("source has no sample"))?
            } else { v.variable.initial_value.clone() };
            let expected = match expected_value {
                SimValue::Boolean(b) => Value::Bool(b),
                SimValue::Double(d) => Value::F64(d),
            };
            if sample.value != expected || sample.quality != 0xc0 || sample.timestamp.ticks() == 0 {
                return Err(Error::unexpected(format!(
                    "invalid configured sample {}",
                    v.item_id
                )));
            }
            println!("PROJECT_READ item={} source={source:?} value={:?} quality={:#x} filetime={} hresult=S_OK",v.item_id,sample.value,sample.quality,sample.timestamp.ticks());
        }
    }
    if !std::env::args().any(|a| a == "--read-only-project-client") {
    let writes: Vec<_> = handles
        .iter()
        .zip(&published)
        .map(|(h, v)| WriteValue {
            server_handle: *h,
            value: match v.variable.initial_value {
                SimValue::Boolean(b) => Value::Bool(b),
                SimValue::Double(d) => Value::F64(d),
            },
        })
        .collect();
    for result in group.write(&writes)? {
        if result
            .as_ref()
            .err()
            .is_none_or(|e| e.code.raw() != 0xc0040006_u32 as i32)
        {
            return Err(Error::unexpected("configured write was not rejected"));
        }
    }
    println!("PROJECT_WRITE_REJECTION_PASS hresult=0xc0040006");
    }
    group.close(true)?;
    println!(
        "PROJECT_COM_CLIENT_PASS server_pid={} client_pid={} revision={}",
        snapshot.pid,
        std::process::id(),
        snapshot.config_revision
    );
    Ok(())
}

#[cfg(test)]
mod diagnostics_tests {
    use super::*;
    use std::io::{Read, Write};
    use std::net::{Shutdown, SocketAddr};
    use std::thread;

    #[test]
    fn com_projection_rejects_missing_timestamp_without_fabricating_one() {
        let mut sample: Sample = serde_json::from_value(serde_json::json!({
            "item_id":"Lab.Value","data_type":"Double","value":1.0,"quality":64,
            "hresult":0,"source_timestamp_filetime":null
        })).unwrap();
        assert_eq!(sample_to_com(sample.clone(), 501).unwrap_err().raw(), 0x80004005u32 as i32);
        sample.source_timestamp_filetime = Some(0);
        assert_eq!(sample_to_com(sample.clone(), 501).unwrap_err().raw(), 0x80004005u32 as i32);
        sample.source_timestamp_filetime = Some(134387114304673403);
        let converted = sample_to_com(sample, 501).unwrap();
        assert_eq!(converted.timestamp.ticks(), 134387114304673403);
        assert_eq!(converted.quality, 64);
        assert_eq!(converted.client_handle.0, 501);
    }

    fn test_service() -> ConfiguredService {
        let runtime = ProjectRuntime::new(Project::default()).expect("valid test project");
        ConfiguredService {
            runtime: Arc::new(Mutex::new(runtime)),
            auth_token: Arc::new("unit-test-token".into()),
            start: Timestamp::from_system_time(SystemTime::now()).expect("timestamp"),
            groups: Mutex::new(HashMap::new()),
        }
    }

    fn token_test_dir(label: &str) -> std::path::PathBuf {
        let nonce = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_nanos();
        let path = std::env::temp_dir().join(format!("opc-da-token-{label}-{}-{nonce}", std::process::id()));
        std::fs::create_dir(&path).unwrap();
        path
    }

    #[test]
    fn auth_token_is_stable_across_server_instances() {
        let dir = token_test_dir("stable");
        let first = load_or_create_auth_token(&dir).unwrap();
        let second = load_or_create_auth_token(&dir).unwrap();
        assert_eq!(first, second);
        assert_eq!(std::fs::read_to_string(dir.join("runtime-auth.token")).unwrap(), first);
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn concurrent_server_instances_share_one_auth_token() {
        let dir = token_test_dir("concurrent");
        let workers: Vec<_> = (0..8).map(|_| {
            let path = dir.clone();
            thread::spawn(move || load_or_create_auth_token(&path).unwrap())
        }).collect();
        let tokens: Vec<_> = workers.into_iter().map(|worker| worker.join().unwrap()).collect();
        assert!(tokens.iter().all(|token| token == &tokens[0]));
        assert_eq!(std::fs::read_to_string(dir.join("runtime-auth.token")).unwrap(), tokens[0]);
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn invalid_auth_token_is_preserved_and_rejected() {
        let dir = token_test_dir("invalid");
        let path = dir.join("runtime-auth.token");
        std::fs::write(&path, "invalid-token").unwrap();
        assert!(load_or_create_auth_token(&dir).is_err());
        assert_eq!(std::fs::read_to_string(&path).unwrap(), "invalid-token");
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn accepted_nonblocking_stream_reads_fragmented_authenticated_post() {
        let listener = TcpListener::bind(SocketAddr::from(([127, 0, 0, 1], 0))).unwrap();
        listener.set_nonblocking(true).unwrap();
        let address = listener.local_addr().unwrap();

        let server = thread::spawn(move || {
            let (mut stream, _) = loop {
                match listener.accept() {
                    Ok(pair) => break pair,
                    Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                        thread::sleep(Duration::from_millis(5));
                    }
                    Err(error) => panic!("accept failed: {error}"),
                }
            };
            // Simulate Windows inheriting the listener's nonblocking mode.
            stream.set_nonblocking(true).unwrap();
            respond(&mut stream, &test_service()).expect("complete HTTP response");
        });

        let mut client = TcpStream::connect(address).unwrap();
        client
            .write_all(
                b"POST /v1/update HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer unit-test-token\r\nContent-Length: 7\r\n\r\n",
            )
            .unwrap();
        thread::sleep(Duration::from_millis(100));
        client.write_all(b"notjson").unwrap();
        client.shutdown(Shutdown::Write).unwrap();

        let mut response = Vec::new();
        client.read_to_end(&mut response).unwrap();
        server.join().unwrap();
        let response = String::from_utf8(response).unwrap();
        assert!(response.starts_with("HTTP/1.0 400 Bad Request"), "{response}");
        assert!(response.contains("JSON"), "{response}");
    }
}
