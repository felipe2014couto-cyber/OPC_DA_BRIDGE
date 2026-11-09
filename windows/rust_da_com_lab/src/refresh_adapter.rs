//! Laboratory-only DA 2.05 connection-point adapters, included in a temporary binding copy.
//!
//! Internal DA 2 laboratory callbacks: Refresh2, Async Read and change subscriptions.
//! No history or client writes. The remaining protocol limits are documented.

use super::*;
use std::sync::atomic::{AtomicBool, AtomicU32, AtomicU8, Ordering};
use std::time::Duration;

use crate::{IOPCAsyncIO2_Impl, IOPCDataCallback};
use opc_classic_utils::ComApartment;
use windows_core::IUnknownImpl;
use windows::Win32::Foundation::FILETIME;
use windows::Win32::System::Com::{
    CLSCTX_INPROC_SERVER, CoCreateInstance, IConnectionPoint, IConnectionPoint_Impl,
    IConnectionPointContainer, IConnectionPointContainer_Impl, IEnumConnectionPoints,
    IEnumConnections, IGlobalInterfaceTable,
};
use windows::Win32::System::Variant::VARIANT;

const CLSID_STD_GIT: GUID = GUID::from_u128(0x00000323_0000_0000_c000_000000000046);
const CONNECT_E_NOCONNECTION: HRESULT = HRESULT(0x80040200_u32 as i32);
const CONNECT_E_ADVISELIMIT: HRESULT = HRESULT(0x80040201_u32 as i32);
const CONNECT_E_CANNOTCONNECT: HRESULT = HRESULT(0x80040202_u32 as i32);
const IID_IOPC_SHUTDOWN: GUID = GUID::from_u128(0xf31dfde1_07b6_11d2_b2d8_0060083ba1fb);

struct Registration {
    cookie: u32,
    git_cookie: u32,
}

pub(super) struct RefreshState {
    registration: Mutex<Option<Registration>>,
    next_cookie: AtomicU32,
    next_cancel: AtomicU32,
    enabled: AtomicBool,
    pending: Mutex<HashMap<u32, Arc<AtomicU8>>>,
}

/// Connection state for the server-level OPC shutdown notification.
pub(super) struct ShutdownState {
    registrations: Mutex<Vec<(u32, u32)>>,
    next_cookie: AtomicU32,
}

impl ShutdownState {
    pub(super) fn new() -> Self {
        Self { registrations: Mutex::new(Vec::new()), next_cookie: AtomicU32::new(1) }
    }
}

impl Drop for ShutdownState {
    fn drop(&mut self) {
        if let Ok(registrations) = self.registrations.get_mut() {
            if !registrations.is_empty() {
                let _apartment = ComApartment::mta().ok();
                if let Ok(table) = git() {
                    for (_, git_cookie) in registrations.drain(..) {
                        let _ = unsafe { table.RevokeInterfaceFromGlobal(git_cookie) };
                    }
                }
            }
        }
    }
}

impl RefreshState {
    pub(super) fn new() -> Self {
        Self {
            registration: Mutex::new(None),
            next_cookie: AtomicU32::new(1),
            next_cancel: AtomicU32::new(1),
            enabled: AtomicBool::new(true),
            pending: Mutex::new(HashMap::new()),
        }
    }
}

impl Drop for RefreshState {
    fn drop(&mut self) {
        if let Ok(slot) = self.registration.get_mut() {
            if let Some(registration) = slot.take() {
                // Drop can occur on a worker after its apartment has ended.
                // If this thread is already STA, CoInitializeEx may reject MTA,
                // but COM is already initialized and the GIT can still be used.
                let _apartment = ComApartment::mta().ok();
                if let Ok(table) = git() {
                    let _ = unsafe { table.RevokeInterfaceFromGlobal(registration.git_cookie) };
                }
            }
        }
    }
}

fn git() -> AbiResult<IGlobalInterfaceTable> {
    unsafe { CoCreateInstance(&CLSID_STD_GIT, None::<&IUnknown>, CLSCTX_INPROC_SERVER) }
}

#[windows_core::implement(IConnectionPoint)]
struct LabConnectionPoint {
    state: Arc<RefreshState>,
    service: Arc<dyn DaGroupService>,
    group_state: Arc<Mutex<GroupState>>,
    container: IConnectionPointContainer,
}

impl LabConnectionPoint {
    fn new(state: Arc<RefreshState>, service: Arc<dyn DaGroupService>, group_state: Arc<Mutex<GroupState>>, container: IConnectionPointContainer) -> Self {
        Self { state, service, group_state, container }
    }
}

impl IConnectionPoint_Impl for LabConnectionPoint_Impl {
    fn GetConnectionInterface(&self) -> AbiResult<GUID> {
        Ok(IOPCDataCallback::IID)
    }

    fn GetConnectionPointContainer(&self) -> AbiResult<IConnectionPointContainer> {
        Ok(self.container.clone())
    }

    fn Advise(&self, sink: windows_core::Ref<'_, IUnknown>) -> AbiResult<u32> {
        let callback: IOPCDataCallback = sink
            .ok()?
            .cast()
            .map_err(|_| AbiError::from_hresult(CONNECT_E_CANNOTCONNECT))?;
        let unknown: IUnknown = callback.cast()?;
        let table = git()?;
        let git_cookie = unsafe { table.RegisterInterfaceInGlobal(&unknown, &IOPCDataCallback::IID)? };
        let mut slot = self.state.registration.lock().map_err(|_| AbiError::from_hresult(E_UNEXPECTED))?;
        if slot.is_some() {
            let _ = unsafe { table.RevokeInterfaceFromGlobal(git_cookie) };
            return Err(AbiError::from_hresult(CONNECT_E_ADVISELIMIT));
        }
        let cookie = self.state.next_cookie.fetch_add(1, Ordering::Relaxed);
        if cookie == 0 {
            let _ = unsafe { table.RevokeInterfaceFromGlobal(git_cookie) };
            return Err(AbiError::from_hresult(E_UNEXPECTED));
        }
        *slot = Some(Registration { cookie, git_cookie });
        drop(slot);
        if let Err(error) = spawn_changes(&self.state, self.service.clone(), self.group_state.clone(), git_cookie) {
            if let Ok(mut slot) = self.state.registration.lock() { slot.take(); }
            let _ = unsafe { table.RevokeInterfaceFromGlobal(git_cookie) };
            return Err(error);
        }
        log_enum("REFRESH_CALLBACK_CONNECT", &format!("cookie={cookie}"));
        Ok(cookie)
    }

    fn Unadvise(&self, cookie: u32) -> AbiResult<()> {
        let mut slot = self.state.registration.lock().map_err(|_| AbiError::from_hresult(E_UNEXPECTED))?;
        if slot.as_ref().map(|r| r.cookie) != Some(cookie) {
            return Err(AbiError::from_hresult(CONNECT_E_NOCONNECTION));
        }
        let git_cookie = slot.as_ref().ok_or_else(|| AbiError::from_hresult(CONNECT_E_NOCONNECTION))?.git_cookie;
        unsafe { git()?.RevokeInterfaceFromGlobal(git_cookie)? };
        slot.take();
        log_enum("REFRESH_CALLBACK_DISCONNECT", &format!("cookie={cookie}"));
        Ok(())
    }

    fn EnumConnections(&self) -> AbiResult<IEnumConnections> {
        Err(AbiError::from_hresult(E_NOTIMPL))
    }
}

impl IConnectionPointContainer_Impl for DaGroupAdapter_Impl {
    fn EnumConnectionPoints(&self) -> AbiResult<IEnumConnectionPoints> {
        Err(AbiError::from_hresult(E_NOTIMPL))
    }

    fn FindConnectionPoint(&self, iid: *const GUID) -> AbiResult<IConnectionPoint> {
        let iid = unsafe { iid.as_ref() }.ok_or_else(|| AbiError::from_hresult(E_POINTER))?;
        if *iid != IOPCDataCallback::IID {
            return Err(AbiError::from_hresult(CONNECT_E_NOCONNECTION));
        }
        Ok(LabConnectionPoint::new(self.refresh.clone(), self.service.clone(), self.state.clone(), self.to_interface()).into())
    }
}

#[windows_core::implement(IConnectionPoint)]
struct ShutdownConnectionPoint {
    state: Arc<ShutdownState>,
    container: IConnectionPointContainer,
}

impl IConnectionPoint_Impl for ShutdownConnectionPoint_Impl {
    fn GetConnectionInterface(&self) -> AbiResult<GUID> { Ok(IID_IOPC_SHUTDOWN) }

    fn GetConnectionPointContainer(&self) -> AbiResult<IConnectionPointContainer> {
        Ok(self.container.clone())
    }

    fn Advise(&self, sink: windows_core::Ref<'_, IUnknown>) -> AbiResult<u32> {
        let sink = sink.ok()?;
        let raw = Interface::as_raw(sink);
        let vtable = unsafe { *(raw as *const *const windows_core::IUnknown_Vtbl) };
        let mut shutdown_interface = ptr::null_mut();
        let query = unsafe { ((*vtable).QueryInterface)(raw, &IID_IOPC_SHUTDOWN, &mut shutdown_interface) };
        if query.0 < 0 || shutdown_interface.is_null() {
            return Err(AbiError::from_hresult(CONNECT_E_CANNOTCONNECT));
        }
        let shutdown_unknown = unsafe { IUnknown::from_raw(shutdown_interface) };
        let table = git()?;
        let git_cookie = unsafe { table.RegisterInterfaceInGlobal(&shutdown_unknown, &IID_IOPC_SHUTDOWN)? };
        let cookie = self.state.next_cookie.fetch_add(1, Ordering::Relaxed);
        if cookie == 0 {
            let _ = unsafe { table.RevokeInterfaceFromGlobal(git_cookie) };
            return Err(AbiError::from_hresult(E_UNEXPECTED));
        }
        self.state.registrations.lock()
            .map_err(|_| AbiError::from_hresult(E_UNEXPECTED))?
            .push((cookie, git_cookie));
        log_enum("SHUTDOWN_CALLBACK_CONNECT", &format!("cookie={cookie}"));
        Ok(cookie)
    }

    fn Unadvise(&self, cookie: u32) -> AbiResult<()> {
        let mut registrations = self.state.registrations.lock()
            .map_err(|_| AbiError::from_hresult(E_UNEXPECTED))?;
        let index = registrations.iter().position(|(registered, _)| *registered == cookie)
            .ok_or_else(|| AbiError::from_hresult(CONNECT_E_NOCONNECTION))?;
        let (_, git_cookie) = registrations.remove(index);
        unsafe { git()?.RevokeInterfaceFromGlobal(git_cookie)? };
        log_enum("SHUTDOWN_CALLBACK_DISCONNECT", &format!("cookie={cookie}"));
        Ok(())
    }

    fn EnumConnections(&self) -> AbiResult<IEnumConnections> {
        Err(AbiError::from_hresult(E_NOTIMPL))
    }
}

impl IConnectionPointContainer_Impl for DaServerAdapter_Impl {
    fn EnumConnectionPoints(&self) -> AbiResult<IEnumConnectionPoints> {
        Err(AbiError::from_hresult(E_NOTIMPL))
    }

    fn FindConnectionPoint(&self, iid: *const GUID) -> AbiResult<IConnectionPoint> {
        let iid = unsafe { iid.as_ref() }.ok_or_else(|| AbiError::from_hresult(E_POINTER))?;
        if *iid != IID_IOPC_SHUTDOWN {
            return Err(AbiError::from_hresult(CONNECT_E_NOCONNECTION));
        }
        let container: IConnectionPointContainer = self.to_interface();
        Ok(ShutdownConnectionPoint { state: self.shutdown.clone(), container }.into())
    }
}

#[derive(Clone, Copy, Debug)]
enum Completion { Read, Refresh }

impl IOPCAsyncIO2_Impl for DaGroupAdapter_Impl {
    fn Read(&self, count: u32, handles: *const u32, transaction: u32,
        cancel: *mut u32, errors: *mut *mut HRESULT) -> AbiResult<()> {
        unsafe {
            if cancel.is_null() || errors.is_null() { return Err(AbiError::from_hresult(E_POINTER)); }
            cancel.write(0); errors.write(ptr::null_mut());
        }
        if count == 0 || count > 4096 { return Err(AbiError::from_hresult(E_INVALIDARG)); }
        let handles = decode_handles(unsafe { borrow_input(handles, count) }.map_err(to_abi_error)?);
        let returned = self.service.validate_read_handles(&handles).map_err(to_abi_error)?;
        if returned.len() != handles.len() { return Err(AbiError::from_hresult(E_UNEXPECTED)); }
        let mut allocated = CoTaskMemArrayBuilder::new(count as usize, NoCleanup).map_err(to_abi_error)?;
        let mut accepted = Vec::new();
        let mut has_error = false;
        for (handle, result) in handles.into_iter().zip(returned) {
            let hr = match result.result {
                Ok(client) => { accepted.push((handle, client)); HRESULT(0) }
                Err(code) => { has_error = true; error_code_to_abi(code) }
            };
            allocated.push(hr).map_err(|_| AbiError::from_hresult(E_UNEXPECTED))?;
        }
        let cookie = registered_cookie(&self.refresh)?;
        let group = self.state.lock().map_err(|_| AbiError::from_hresult(E_UNEXPECTED))?.client_handle;
        let cancel_id = if accepted.is_empty() { 0 } else {
            spawn_request(self.refresh.clone(), self.service.clone(), cookie, DataSource::Device,
                transaction, group, accepted, Completion::Read)?
        };
        let (error_ptr, _) = allocated.finish().map_err(to_abi_error)?.into_raw_parts();
        unsafe { cancel.write(cancel_id); errors.write(error_ptr); }
        log_enum("ASYNC_READ_REQUEST", &format!("transaction={transaction} count={count} cancel={cancel_id} partial={has_error}"));
        if has_error { Err(AbiError::from_hresult(HRESULT(1))) } else { Ok(()) }
    }

    fn Write(&self, count: u32, handles: *const u32, values: *const VARIANT,
        _transaction: u32, cancel: *mut u32, errors: *mut *mut HRESULT) -> AbiResult<()> {
        unsafe {
            if cancel.is_null() || errors.is_null() { return Err(AbiError::from_hresult(E_POINTER)); }
            cancel.write(0); errors.write(ptr::null_mut());
        }
        if count == 0 || count > 4096 { return Err(AbiError::from_hresult(E_INVALIDARG)); }
        let _ = unsafe { borrow_input(handles, count) }.map_err(to_abi_error)?;
        let _ = unsafe { borrow_input(values, count) }.map_err(to_abi_error)?;
        let mut allocated = CoTaskMemArrayBuilder::new(count as usize, NoCleanup).map_err(to_abi_error)?;
        for _ in 0..count { allocated.push(HRESULT(0xc0040006u32 as i32)).map_err(|_| AbiError::from_hresult(E_UNEXPECTED))?; }
        let (error_ptr, _) = allocated.finish().map_err(to_abi_error)?.into_raw_parts();
        unsafe { errors.write(error_ptr); }
        log_enum("ASYNC_WRITE_REJECTED", &format!("count={count} hresult=0xc0040006"));
        Err(AbiError::from_hresult(HRESULT(1)))
    }

    fn Refresh2(&self, source: crate::tagOPCDATASOURCE, transaction: u32) -> AbiResult<u32> {
        let source = if source == crate::OPC_DS_CACHE { DataSource::Cache }
            else if source == crate::OPC_DS_DEVICE { DataSource::Device }
            else { return Err(AbiError::from_hresult(E_INVALIDARG)); };
        let state = self.state.lock().map_err(|_| AbiError::from_hresult(E_UNEXPECTED))?.clone();
        if !state.active { return Err(AbiError::from_hresult(E_FAIL)); }
        let items = self.service.active_refresh_items().map_err(to_abi_error)?;
        if items.is_empty() { return Err(AbiError::from_hresult(E_FAIL)); }
        let cookie = registered_cookie(&self.refresh)?;
        let count = items.len();
        let cancel = spawn_request(self.refresh.clone(), self.service.clone(), cookie, source,
            transaction, state.client_handle, items, Completion::Refresh)?;
        log_enum("REFRESH_REQUEST", &format!("transaction={transaction} source={source:?} count={count} cancel={cancel}"));
        Ok(cancel)
    }

    fn Cancel2(&self, cancel: u32) -> AbiResult<()> {
        let pending = self.refresh.pending.lock().map_err(|_| AbiError::from_hresult(E_UNEXPECTED))?;
        let job = pending.get(&cancel).ok_or_else(|| AbiError::from_hresult(E_FAIL))?;
        job.compare_exchange(0, 1, Ordering::AcqRel, Ordering::Acquire)
            .map_err(|_| AbiError::from_hresult(E_FAIL))?;
        log_enum("ASYNC_CANCEL_REQUEST", &format!("cancel={cancel}"));
        Ok(())
    }
    fn SetEnable(&self, enable: windows_core::BOOL) -> AbiResult<()> {
        registered_cookie(&self.refresh)?;
        self.refresh.enabled.store(enable.as_bool(), Ordering::Release);
        log_enum("CALLBACK_ENABLE", &format!("enabled={}", enable.as_bool()));
        Ok(())
    }
    fn GetEnable(&self) -> AbiResult<windows_core::BOOL> {
        registered_cookie(&self.refresh)?;
        Ok(self.refresh.enabled.load(Ordering::Acquire).into())
    }
}

fn registered_cookie(refresh: &RefreshState) -> AbiResult<u32> {
    refresh.registration.lock().map_err(|_| AbiError::from_hresult(E_UNEXPECTED))?
        .as_ref().map(|r| r.git_cookie).ok_or_else(|| AbiError::from_hresult(CONNECT_E_NOCONNECTION))
}
fn callback_for(refresh: &RefreshState, cookie: u32) -> AbiResult<IOPCDataCallback> {
    if registered_cookie(refresh)? != cookie { return Err(AbiError::from_hresult(CONNECT_E_NOCONNECTION)); }
    let mut raw = ptr::null_mut();
    unsafe { git()?.GetInterfaceFromGlobal(cookie, &IOPCDataCallback::IID, &mut raw)? };
    if raw.is_null() { return Err(AbiError::from_hresult(E_POINTER)); }
    Ok(unsafe { IOPCDataCallback::from_raw(raw) })
}

fn spawn_request(refresh: Arc<RefreshState>, service: Arc<dyn DaGroupService>, cookie: u32,
    source: DataSource, transaction: u32, group: u32,
    items: Vec<(ServerItemHandle, ClientItemHandle)>, completion: Completion) -> AbiResult<u32> {
    let cancel = refresh.next_cancel.fetch_update(Ordering::Relaxed, Ordering::Relaxed, |v| v.checked_add(1))
        .map_err(|_| AbiError::from_hresult(E_UNEXPECTED))?;
    let job = Arc::new(AtomicU8::new(0));
    {
        let mut pending = refresh.pending.lock().map_err(|_| AbiError::from_hresult(E_UNEXPECTED))?;
        if pending.len() >= 64 { return Err(AbiError::from_hresult(E_OUTOFMEMORY)); }
        pending.insert(cancel, job.clone());
    }
    let worker_refresh = refresh.clone();
    let spawn = std::thread::Builder::new().name("lab-da-request".into()).spawn(move || {
        // Return the request/cancel handle before sending the completion.
        std::thread::sleep(Duration::from_millis(50));
        let result = (|| -> AbiResult<()> {
            let _apartment = ComApartment::mta().map_err(to_abi_error)?;
            let callback = callback_for(&worker_refresh, cookie)?;
            match job.compare_exchange(0, 2, Ordering::AcqRel, Ordering::Acquire) {
                Ok(_) => {
                    let handles: Vec<_> = items.iter().map(|(h, _)| *h).collect();
                    let samples = service.read(source, &handles).map_err(to_abi_error)?;
                    send_samples(&callback, completion, transaction, group, &items, samples)?;
                }
                Err(1) => {
                    unsafe { callback.OnCancelComplete(transaction, group)?; }
                    log_enum("ASYNC_CANCEL_COMPLETE", &format!("transaction={transaction} cancel={cancel}"));
                }
                Err(_) => return Err(AbiError::from_hresult(E_UNEXPECTED)),
            }
            Ok(())
        })();
        if let Err(error) = result {
            log_enum("CALLBACK_ERROR", &format!("method={completion:?} transaction={transaction} hresult={:#010x}", error.code().0 as u32));
        }
        if let Ok(mut pending) = worker_refresh.pending.lock() { pending.remove(&cancel); }
    });
    if spawn.is_err() {
        if let Ok(mut pending) = refresh.pending.lock() { pending.remove(&cancel); }
        return Err(AbiError::from_hresult(E_OUTOFMEMORY));
    }
    Ok(cancel)
}

fn send_samples(callback: &IOPCDataCallback, completion: Completion, transaction: u32, group: u32,
    items: &[(ServerItemHandle, ClientItemHandle)], samples: Vec<ServerItemResult<ServerSample>>) -> AbiResult<()> {
    if samples.len() != items.len() { return Err(AbiError::from_hresult(E_UNEXPECTED)); }
    let mut clients = Vec::with_capacity(items.len());
    let mut values = Vec::with_capacity(items.len());
    let mut qualities = Vec::with_capacity(items.len());
    let mut timestamps: Vec<FILETIME> = Vec::with_capacity(items.len());
    let mut errors = Vec::with_capacity(items.len());
    for ((handle, client), result) in items.iter().zip(samples) {
        clients.push(client.0);
        match result.result {
            Ok(sample) => match value_to_abi(&sample.value) {
                Ok(value) => {
                    log_enum("CALLBACK_ITEM", &format!("method={completion:?} transaction={transaction} server_handle={} client_handle={} value={:?} quality={:#04x} filetime={} hresult=0x00000000",handle.raw(), client.0, sample.value, sample.quality, sample.timestamp.ticks()));
                    values.push(value); qualities.push(sample.quality); timestamps.push(timestamp_to_abi(sample.timestamp)); errors.push(HRESULT(0));
                }
                Err(error) => { values.push(VARIANT::default()); qualities.push(0); timestamps.push(FILETIME::default()); errors.push(error_code_to_abi(error.code())); }
            },
            Err(error) => {
                log_enum("CALLBACK_ITEM", &format!("method={completion:?} transaction={transaction} client_handle={} hresult={:#010x}",client.0,error.raw() as u32));
                values.push(VARIANT::default()); qualities.push(0); timestamps.push(FILETIME::default()); errors.push(error_code_to_abi(error));
            }
        }
    }
    let master_quality = if qualities.iter().all(|q| q & 0xc0 == 0xc0) { HRESULT(0) } else { HRESULT(1) };
    let master_error = if errors.iter().all(|e| e.0 >= 0) { HRESULT(0) } else { HRESULT(1) };
    unsafe {
        match completion {
            Completion::Read => callback.OnReadComplete(transaction, group, master_quality, master_error, clients.len() as u32,
                clients.as_ptr(), values.as_ptr(), qualities.as_ptr(), timestamps.as_ptr(), errors.as_ptr())?,
            Completion::Refresh => callback.OnDataChange(transaction, group, master_quality, master_error, clients.len() as u32,
                clients.as_ptr(), values.as_ptr(), qualities.as_ptr(), timestamps.as_ptr(), errors.as_ptr())?,
        }
    }
    log_enum(if matches!(completion, Completion::Read) { "ASYNC_READ_CALLBACK" } else if transaction == 0 { "ADVISE_CALLBACK" } else { "REFRESH_CALLBACK" },
        &format!("transaction={transaction} group={group} count={} result=S_OK", clients.len()));
    Ok(())
}

fn spawn_changes(refresh: &Arc<RefreshState>, service: Arc<dyn DaGroupService>, state: Arc<Mutex<GroupState>>, cookie: u32) -> AbiResult<()> {
    let weak = Arc::downgrade(refresh);
    std::thread::Builder::new().name("lab-da-advise".into()).spawn(move || {
        let Ok(_apartment) = ComApartment::mta() else { return; };
        let mut previous: HashMap<u32, String> = HashMap::new();
        loop {
            let Some(refresh) = weak.upgrade() else { break; };
            if registered_cookie(&refresh).ok() != Some(cookie) { break; }
            let Ok(group) = state.lock().map(|s| s.clone()) else { break; };
            let delay = Duration::from_millis(group.update_rate.max(10) as u64);
            if group.active && refresh.enabled.load(Ordering::Acquire) {
                let result = (|| -> AbiResult<()> {
                    let items = service.active_refresh_items().map_err(to_abi_error)?;
                    let handles: Vec<_> = items.iter().map(|(h,_)| *h).collect();
                    let samples = service.read(DataSource::Cache, &handles).map_err(to_abi_error)?;
                    if samples.len() != items.len() { return Err(AbiError::from_hresult(E_UNEXPECTED)); }
                    let mut next = HashMap::new(); let mut changed_items = Vec::new(); let mut changed_samples = Vec::new();
                    for (item, sample) in items.into_iter().zip(samples) {
                        // A timestamp-only change does not manufacture a value-change event.
                        let signature = match &sample.result {
                            Ok(s) => format!("{}:{:?}:{}",item.1.0,s.value,s.quality),
                            Err(e) => format!("{}:error:{}",item.1.0,e.raw()),
                        };
                        if previous.get(&item.0.raw()) != Some(&signature) { changed_items.push(item); changed_samples.push(sample); }
                        next.insert(item.0.raw(), signature);
                    }
                    if !changed_items.is_empty() {
                        let callback = callback_for(&refresh, cookie)?;
                        send_samples(&callback, Completion::Refresh, 0, group.client_handle, &changed_items, changed_samples)?;
                    }
                    previous = next;
                    Ok(())
                })();
                if let Err(error) = result { log_enum("ADVISE_ERROR", &format!("hresult={:#010x}",error.code().0 as u32)); }
            } else { previous.clear(); }
            drop(refresh); // the worker must not keep a released COM group alive.
            // Bound unsubscribe/group-release latency even with a very long update rate.
            let mut remaining = delay;
            while !remaining.is_zero() {
                let step = remaining.min(Duration::from_millis(100)); std::thread::sleep(step); remaining -= step;
                let Some(current) = weak.upgrade() else { return; };
                if registered_cookie(&current).ok() != Some(cookie) { return; }
            }
        }
    }).map(|_| ()).map_err(|_| AbiError::from_hresult(E_OUTOFMEMORY))
}
