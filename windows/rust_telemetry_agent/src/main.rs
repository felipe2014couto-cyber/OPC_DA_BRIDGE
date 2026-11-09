// SPDX-License-Identifier: MIT
//! Bounded stdin/SSH adapter for the existing loopback laboratory runtime.
//! No OPC definitions, registry, services, arbitrary commands or network listener.
use serde_json::{json, Value};
use std::fs;
use std::io::{self, BufRead, BufReader, Read, Write};
use std::net::{Ipv4Addr, SocketAddr, TcpStream, UdpSocket};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::mpsc;
use std::time::{Duration, Instant};

const LIMIT: usize = 2_097_152;

fn http(port: u16, route: &str, body: Option<(&Value, &str)>) -> Result<Value, ()> {
    let address = SocketAddr::from((Ipv4Addr::LOCALHOST, port));
    let mut stream = TcpStream::connect_timeout(&address, Duration::from_secs(2)).map_err(|_| ())?;
    stream.set_read_timeout(Some(Duration::from_secs(3))).map_err(|_| ())?;
    stream.set_write_timeout(Some(Duration::from_secs(3))).map_err(|_| ())?;
    let request = if let Some((payload, token)) = body {
        if token.is_empty() || token.contains(['\r', '\n']) { return Err(()); }
        let bytes = serde_json::to_vec(payload).map_err(|_| ())?;
        let mut head = format!("POST {route} HTTP/1.0\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\nAuthorization: Bearer {token}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n", bytes.len()).into_bytes();
        head.extend_from_slice(&bytes); head
    } else {
        format!("GET {route} HTTP/1.0\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n").into_bytes()
    };
    stream.write_all(&request).map_err(|_| ())?;
    let mut bytes = Vec::new();
    stream.take((LIMIT + 1) as u64).read_to_end(&mut bytes).map_err(|_| ())?;
    if bytes.len() > LIMIT { return Err(()); }
    let split = bytes.windows(4).position(|s| s == b"\r\n\r\n").ok_or(())?;
    let header = std::str::from_utf8(&bytes[..split]).map_err(|_| ())?;
    if !matches!(header.lines().next().and_then(|s| s.split_whitespace().nth(1)), Some("200")) { return Err(()); }
    serde_json::from_slice(&bytes[split + 4..]).map_err(|_| ())
}

#[cfg(windows)]
fn host_name() -> Result<String, ()> {
    #[link(name = "kernel32")]
    extern "system" { fn GetComputerNameW(buffer: *mut u16, size: *mut u32) -> i32; }
    let mut data = [0u16; 256]; let mut size = data.len() as u32;
    if unsafe { GetComputerNameW(data.as_mut_ptr(), &mut size) } == 0 { return Err(()); }
    String::from_utf16(&data[..size as usize]).map_err(|_| ())
}
#[cfg(not(windows))]
fn host_name() -> Result<String, ()> { Err(()) }

#[cfg(windows)]
fn process_identity(pid: u32, executable: &Path) -> Result<u64, ()> {
    use std::ffi::c_void;
    #[repr(C)] struct FileTime { low: u32, high: u32 }
    #[link(name = "kernel32")]
    extern "system" {
        fn OpenProcess(access: u32, inherit: i32, pid: u32) -> *mut c_void;
        fn QueryFullProcessImageNameW(process: *mut c_void, flags: u32, name: *mut u16, size: *mut u32) -> i32;
        fn GetProcessTimes(process: *mut c_void, created: *mut FileTime, exit: *mut FileTime, kernel: *mut FileTime, user: *mut FileTime) -> i32;
        fn CloseHandle(handle: *mut c_void) -> i32;
    }
    // PROCESS_QUERY_LIMITED_INFORMATION, supported since Windows Vista.
    let handle = unsafe { OpenProcess(0x1000, 0, pid) };
    if handle.is_null() { return Err(()); }
    let result = (|| {
        let mut name = vec![0u16; 32768]; let mut size = name.len() as u32;
        if unsafe { QueryFullProcessImageNameW(handle, 0, name.as_mut_ptr(), &mut size) } == 0 { return Err(()); }
        let path = PathBuf::from(String::from_utf16(&name[..size as usize]).map_err(|_| ())?);
        // Both paths must exist and resolve to the same canonical path.
        if fs::canonicalize(path).map_err(|_| ())?.to_string_lossy().to_lowercase()
            != fs::canonicalize(executable).map_err(|_| ())?.to_string_lossy().to_lowercase() { return Err(()); }
        let mut created = FileTime { low: 0, high: 0 };
        let mut exit = FileTime { low: 0, high: 0 }; let mut kernel = FileTime { low: 0, high: 0 }; let mut user = FileTime { low: 0, high: 0 };
        if unsafe { GetProcessTimes(handle, &mut created, &mut exit, &mut kernel, &mut user) } == 0 { return Err(()); }
        if exit.low != 0 || exit.high != 0 { return Err(()); }
        Ok((created.high as u64) << 32 | created.low as u64)
    })();
    unsafe { CloseHandle(handle); } result
}
#[cfg(not(windows))]
fn process_identity(_: u32, _: &Path) -> Result<u64, ()> { Err(()) }

struct Runtime {
    directory: PathBuf, binding_bytes: Vec<u8>, binding: Value,
    pid: u32, created: u64, port: u16,
}
impl Runtime {
    fn open(directory: PathBuf) -> Result<Self, ()> {
        let binding_bytes = fs::read(directory.join("identity.json")).map_err(|_| ())?;
        if binding_bytes.len() > 65536 { return Err(()); }
        let binding: Value = serde_json::from_slice(&binding_bytes).map_err(|_| ())?;
        if !host_name()?.eq_ignore_ascii_case(binding["expected_hostname"].as_str().ok_or(())?) { return Err(()); }
        let ip: Ipv4Addr = binding["expected_address"].as_str().ok_or(())?.parse().map_err(|_| ())?;
        // Binding an ephemeral UDP socket checks the address belongs to this
        // host without contacting another machine or opening a listener.
        let _local = UdpSocket::bind((ip, 0)).map_err(|_| ())?;
        let port = u16::try_from(binding["diagnostic_port"].as_u64().ok_or(())?).map_err(|_| ())?;
        if port == 0 { return Err(()); }
        let snapshot = http(port, "/v1/snapshot", None)?;
        if snapshot["machine_id"] != binding["machine_id"] { return Err(()); }
        let pid = u32::try_from(snapshot["pid"].as_u64().ok_or(())?).map_err(|_| ())?;
        let created = process_identity(pid, &directory.join("rust-da-com-lab.exe"))?;
        Ok(Self { directory, binding_bytes, binding, pid, created, port })
    }
    fn request(&self, envelope: &Value) -> Result<Value, ()> {
        if fs::read(self.directory.join("identity.json")).map_err(|_| ())? != self.binding_bytes
            || process_identity(self.pid, &self.directory.join("rust-da-com-lab.exe"))? != self.created { return Err(()); }
        if envelope["machine_id"] != self.binding["machine_id"]
            || envelope["expected_hostname"] != self.binding["expected_hostname"]
            || envelope["expected_address"] != self.binding["expected_address"] { return Err(()); }
        let operation = envelope["operation"].as_str().ok_or(())?;
        let mut reply = if operation == "probe" {
            let snapshot = http(self.port, "/v1/snapshot", None)?;
            if snapshot["pid"] != self.pid || snapshot["machine_id"] != self.binding["machine_id"] { return Err(()); }
            json!({"ok":true,"pid":self.pid,"probe_only":true})
        } else {
            if envelope["schema_version"] != 1 || !valid_id(envelope["deployment_id"].as_str().ok_or(())?) { return Err(()); }
            let route = match operation { "source-session" => "/v1/source/session", "source-samples" => "/v1/source/samples", _ => return Err(()) };
            let token = fs::read_to_string(self.directory.join("runtime-auth.token")).map_err(|_| ())?;
            http(self.port, route, Some((envelope, token.trim())))?
        };
        if reply["ok"] != true || reply["pid"] != self.pid { return Err(()); }
        reply["agent_pid"] = json!(std::process::id());
        Ok(reply)
    }
}

fn valid_id(id: &str) -> bool {
    id.len() == 36 && id.bytes().enumerate().all(|(i,c)|
        if [8,13,18,23].contains(&i) { c == b'-' } else { c.is_ascii_hexdigit() })
}

struct SourceWorker { child: Child, directory: PathBuf, binding_path: PathBuf, binding_bytes: Vec<u8>, _lock: fs::File }
impl SourceWorker {
    fn open(root: PathBuf) -> Result<Self, ()> {
        let mut options = fs::OpenOptions::new(); options.create(true).write(true);
        #[cfg(windows)] { use std::os::windows::fs::OpenOptionsExt; options.share_mode(0); }
        let lock = options.open(root.join("source-stream.lock")).map_err(|_| ())?;
        let binding_path = root.join("source-identity.json");
        let binding_bytes = fs::read(&binding_path).map_err(|_| ())?;
        if binding_bytes.len() > 65536 { return Err(()); }
        let binding: Value = serde_json::from_slice(&binding_bytes).map_err(|_| ())?;
        if !host_name()?.eq_ignore_ascii_case(binding["expected_hostname"].as_str().ok_or(())?) { return Err(()); }
        let ip: Ipv4Addr = binding["expected_address"].as_str().ok_or(())?.parse().map_err(|_| ())?;
        let _local = UdpSocket::bind((ip, 0)).map_err(|_| ())?;
        // Unique owned IPC folder; no persistent config is overwritten.
        let nonce = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map_err(|_| ())?.as_nanos();
        let directory = root.join(format!("session-{}-{nonce}", std::process::id()));
        fs::create_dir(&directory).map_err(|_| ())?;
        // Use the existing system runtime, always STA and with stdin closed.
        // Only this fixed worker script can be launched; no command from JSON.
        let windows = std::env::var_os("SystemRoot").ok_or(())?;
        let powershell = PathBuf::from(windows).join("System32/WindowsPowerShell/v1.0/powershell.exe");
        let child = Command::new(powershell).args(["-STA", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File"])
            .arg(root.join("opc_da_stream_worker.ps1")).arg("-SessionDirectory").arg(&directory)
            .stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null()).spawn();
        match child {
            Ok(child) => Ok(Self { child, directory, binding_path, binding_bytes, _lock: lock }),
            Err(_) => { let _ = fs::remove_dir(&directory); Err(()) }
        }
    }
    fn request(&mut self, payload: &Value) -> Result<Value, ()> {
        if payload["operation"] != "read" || payload["transport"] != "opc_da_automation"
            || fs::read(&self.binding_path).map_err(|_| ())? != self.binding_bytes { return Err(()); }
        let bytes = serde_json::to_vec(payload).map_err(|_| ())?;
        if bytes.len() > 65536 { return Err(()); }
        let path = self.directory.join("request.json");
        let temporary = self.directory.join("request.tmp");
        if path.exists() || temporary.exists() { return Err(()); }
        fs::write(&temporary, bytes).map_err(|_| ())?;
        fs::rename(&temporary, &path).map_err(|_| ())?;
        let started = Instant::now();
        let result = self.directory.join("response.json");
        loop {
            if self.child.try_wait().map_err(|_| ())?.is_some() { return Err(()); }
            if result.exists() {
                let bytes = fs::read(&result).map_err(|_| ())?;
                if bytes.len() > LIMIT { return Err(()); }
                let reply: Value = serde_json::from_slice(&bytes).map_err(|_| ())?;
                fs::remove_file(&result).map_err(|_| ())?;
                return Ok(reply);
            }
            // Bounds COM hangs in the owned child. Drop sends stop.flag,
            // then terminates only this child if normal shutdown fails.
            if started.elapsed() >= Duration::from_secs(5) { return Err(()); }
            std::thread::sleep(Duration::from_millis(2));
        }
    }
}
impl Drop for SourceWorker {
    fn drop(&mut self) {
        let _ = fs::write(self.directory.join("stop.flag"), b"stop");
        let started = Instant::now();
        loop {
            match self.child.try_wait() {
                Ok(Some(_)) => break,
                _ if started.elapsed() >= Duration::from_secs(2) => { let _ = self.child.kill(); let _ = self.child.wait(); break; }
                _ => std::thread::sleep(Duration::from_millis(10)),
            }
        }
        let _ = fs::remove_dir_all(&self.directory);
    }
}

enum Agent { Destination(Runtime), Source(SourceWorker) }
impl Agent {
    fn request(&mut self, payload: &Value) -> Result<Value, ()> {
        match self { Self::Destination(runtime) => runtime.request(payload), Self::Source(worker) => worker.request(payload) }
    }
}

fn run() -> Result<(), ()> {
    let args: Vec<_> = std::env::args_os().collect();
    if args.len() != 3 { return Err(()); }
    let mut agent = if args[1] == "--lab-directory" { Agent::Destination(Runtime::open(PathBuf::from(&args[2]))?) }
        else if args[1] == "--source-directory" { Agent::Source(SourceWorker::open(PathBuf::from(&args[2]))?) }
        else { return Err(()); };
    let (tx, rx) = mpsc::sync_channel(1);
    std::thread::spawn(move || {
        let stdin = io::stdin(); let mut reader = BufReader::new(stdin.lock());
        loop {
            let mut bytes = Vec::new();
            let read = reader.by_ref().take((LIMIT + 1) as u64).read_until(b'\n', &mut bytes);
            if read.is_err() || bytes.is_empty() { break; }
            let valid = bytes.len() <= LIMIT && bytes.last() == Some(&b'\n');
            if tx.send(if valid { Ok(bytes) } else { Err(()) }).is_err() || !valid { break; }
        }
    });
    let started = Instant::now();
    for _ in 0..512 {
        let remaining = Duration::from_secs(300).saturating_sub(started.elapsed());
        if remaining.is_zero() { break; }
        let bytes = match rx.recv_timeout(Duration::from_secs(15).min(remaining)) {
            Ok(bytes) => bytes?, Err(_) => break,
        };
        let frame: Value = serde_json::from_slice(&bytes).map_err(|_| ())?;
        let id = frame["request_id"].as_str().filter(|s| valid_id(s)).ok_or(())?;
        let clock = Instant::now();
        let result = agent.request(&frame["payload"]);
        let failed = result.is_err();
        let mut reply = result.unwrap_or_else(|_| json!({"ok":false,"error_code":"stream_request_failed"}));
        reply["agent_request_ms"] = json!(clock.elapsed().as_secs_f64() * 1000.0);
        let response = serde_json::to_vec(&json!({"request_id":id,"reply":reply})).map_err(|_| ())?;
        let mut output = io::stdout().lock();
        output.write_all(&response).and_then(|_| output.write_all(b"\n")).and_then(|_| output.flush()).map_err(|_| ())?;
        if failed { return Err(()); }
    }
    Ok(())
}
fn main() {
    if run().is_err() { eprintln!("TELEMETRY_AGENT_FAILED"); std::process::exit(1); }
}

#[cfg(test)] mod tests {
    use super::*;
    #[test] fn correlation_id_validation() {
        assert!(valid_id("7a2f45b1-64d3-4e8c-9a11-3c852b7f2140"));
        for id in ["", "../payload", "7a2f45b164d34e8c9a113c852b7f21400000", "zzzzzzzz-64d3-4e8c-9a11-3c852b7f2140"] { assert!(!valid_id(id)); }
    }
    #[test] fn http_is_loopback_and_checks_response() {
        let listener = std::net::TcpListener::bind((Ipv4Addr::LOCALHOST,0)).unwrap();
        let port = listener.local_addr().unwrap().port();
        let thread = std::thread::spawn(move || { let (mut s, _) = listener.accept().unwrap();
            let mut request=[0;1024]; let n=s.read(&mut request).unwrap();
            let text=std::str::from_utf8(&request[..n]).unwrap();assert!(text.starts_with("GET /v1/snapshot"));
            s.write_all(b"HTTP/1.0 200 OK\r\nContent-Length: 11\r\n\r\n{\"ok\":true}").unwrap(); });
        assert_eq!(http(port,"/v1/snapshot",None).unwrap()["ok"],true);thread.join().unwrap();
    }
}
