//! Read-only local COM diagnostics. No registration, OPC writes or network DCOM.
use super::*;
use opc_da_bindings::{IOPCGroupStateMgt, IOPCAsyncIO2, IOPCDataCallback, IOPCDataCallback_Impl, OPC_DS_CACHE, OPC_DS_DEVICE};
use windows::Win32::Foundation::FILETIME;
use windows::Win32::System::Com::IConnectionPointContainer;
use windows::Win32::System::Variant::VARIANT;
use windows_core::{HRESULT, Result as AbiResult};

type Events = Arc<Mutex<Vec<serde_json::Value>>>;
#[windows_core::implement(IOPCDataCallback)]
struct Probe { events: Events }
impl Probe_Impl {
    fn record(&self, method: &str, transaction: u32, group: u32, quality: HRESULT, error: HRESULT,
        count: u32, clients: *const u32, values: *const VARIANT, qualities: *const u16,
        times: *const FILETIME, errors: *const HRESULT) -> AbiResult<()> {
        if count > 4096 || (count > 0 && (clients.is_null() || values.is_null() || qualities.is_null() || times.is_null() || errors.is_null())) {
            return Err(windows_core::Error::from_hresult(HRESULT(0x80004003u32 as i32)));
        }
        let mut samples = Vec::new();
        for i in 0..count as usize {
            unsafe {
                let v = &(*values.add(i)).Anonymous.Anonymous;
                let value = match v.vt.0 { 5 => serde_json::json!(v.Anonymous.dblVal), 11 => serde_json::json!(v.Anonymous.boolVal.0 != 0), _ => serde_json::Value::Null };
                let time = *times.add(i);
                samples.push(serde_json::json!({"client":*clients.add(i),"vt":v.vt.0,"value":value,
                    "quality":*qualities.add(i),"filetime":((time.dwHighDateTime as u64)<<32)|time.dwLowDateTime as u64,"hresult":(*errors.add(i)).0}));
            }
        }
        let event = serde_json::json!({"method":method,"transaction":transaction,"group":group,
            "master_quality":quality.0,"master_error":error.0,"samples":samples});
        println!("CALLBACK_PROBE {event}");
        self.events.lock().map_err(|_| windows_core::Error::from_hresult(HRESULT(0x8000ffffu32 as i32)))?.push(event);
        Ok(())
    }
}
impl IOPCDataCallback_Impl for Probe_Impl {
    fn OnDataChange(&self,t:u32,g:u32,q:HRESULT,e:HRESULT,n:u32,c:*const u32,v:*const VARIANT,qs:*const u16,ts:*const FILETIME,es:*const HRESULT)->AbiResult<()> { self.record("change",t,g,q,e,n,c,v,qs,ts,es) }
    fn OnReadComplete(&self,t:u32,g:u32,q:HRESULT,e:HRESULT,n:u32,c:*const u32,v:*const VARIANT,qs:*const u16,ts:*const FILETIME,es:*const HRESULT)->AbiResult<()> { self.record("read",t,g,q,e,n,c,v,qs,ts,es) }
    fn OnWriteComplete(&self,_t:u32,_g:u32,_e:HRESULT,_n:u32,_c:*const u32,_es:*const HRESULT)->AbiResult<()> { Err(windows_core::Error::from_hresult(HRESULT(0x80004001u32 as i32))) }
    fn OnCancelComplete(&self,t:u32,g:u32)->AbiResult<()> {
        self.events.lock().unwrap().push(serde_json::json!({"method":"cancel","transaction":t,"group":g})); Ok(())
    }
}
fn err(e: windows_core::Error) -> Error { Error::unexpected(format!("COM HRESULT={:#010x}",e.code().0 as u32)) }
fn wait_event(events:&Events,method:&str,transaction:u32)->Result<serde_json::Value> {
    for _ in 0..100 {
        if let Some(e)=events.lock().unwrap().iter().find(|e|e["method"]==method && e["transaction"]==transaction).cloned(){return Ok(e);}
        std::thread::sleep(Duration::from_millis(50));
    }
    Err(Error::unexpected(format!("callback timeout method={method} transaction={transaction}")))
}
fn check_samples(event:&serde_json::Value,count:usize)->Result<()> {
    let samples=event["samples"].as_array().ok_or_else(||Error::unexpected("missing callback samples"))?;
    if samples.len()!=count || samples.iter().any(|s|s["hresult"]!=0 || s["filetime"].as_u64().unwrap_or(0)==0 || ![5,11].contains(&s["vt"].as_u64().unwrap_or(0))){
        return Err(Error::unexpected(format!("invalid callback samples {event}")));
    }
    Ok(())
}
fn wait_quality(events:&Events,start:usize,good:bool)->Result<serde_json::Value> {
    for _ in 0..100 {
        if let Some(event)=events.lock().unwrap().iter().skip(start).find(|e|e["method"]=="change" && e["transaction"]==0 &&
            e["samples"].as_array().is_some_and(|ss|!ss.is_empty()&&ss.iter().all(|s|(s["quality"].as_u64().unwrap_or(0)&192==192)==good))).cloned(){return Ok(event);}
        std::thread::sleep(Duration::from_millis(50));
    }
    Err(Error::unexpected(format!("no unsolicited quality transition good={good}")))
}
struct OwnFlag(std::path::PathBuf);
impl Drop for OwnFlag {fn drop(&mut self){let _=std::fs::remove_file(&self.0);}}
pub fn async_test()->Result<()> {
    let apartment=ComApartment::mta()?;
    let client=DaClient::connect_class_id(&apartment,&runtime_clsid(),opc_classic_types::ClassContext::LOCAL_SERVER)?;
    let mut options=GroupOptions::new("RustAsyncInterprocessProbe");options.requested_update_rate=200;options.client_handle=777;
    let group=client.add_group(options)?;
    let ids=if std::env::args().any(|a|a=="--async-project-client-test") {
        let project=opc_da_project::Project::load(&configured::project_path()?).map_err(Error::unexpected)?;
        project.published_variables().into_iter().filter(|v|v.variable.source.is_some()).map(|v|v.item_id).collect::<Vec<_>>()
    }else{vec!["RustLab.Numeric.Double".into(),"RustLab.Boolean".into()]};
    if ids.len()!=2 {return Err(Error::unexpected("probe requires exactly two selected source items"));}
    let added=group.add_items(&[ItemSpec::new(&ids[0],501),ItemSpec::new(&ids[1],502)])?;
    let handles=added.into_iter().map(|r|r.map(|i|i.server_handle).map_err(|e|Error::unexpected(e.to_string()))).collect::<Result<Vec<_>>>()?;
    let unknown=unsafe {IUnknown::from_raw(group.object().into_raw())};
    let cp: IConnectionPointContainer=unknown.cast().map_err(err)?;
    let io:IOPCAsyncIO2=unknown.cast().map_err(err)?;
    let group_io:IOPCGroupStateMgt=unknown.cast().map_err(err)?;
    let point=unsafe{cp.FindConnectionPoint(&IOPCDataCallback::IID)}.map_err(err)?;
    let events=Arc::new(Mutex::new(Vec::new()));
    let sink:IOPCDataCallback=Probe{events:events.clone()}.into();
    let cookie=unsafe{point.Advise(&sink)}.map_err(err)?;
    let result=(|| -> Result<()> {
        unsafe{io.SetEnable(false)}.map_err(err)?;
        std::thread::sleep(Duration::from_millis(250));events.lock().unwrap().clear();
        for source in [DataSource::Cache,DataSource::Device] {
            let samples=group.read(source,&handles)?;
            for (i,s) in samples.into_iter().enumerate(){let s=s.map_err(|e|Error::unexpected(e.to_string()))?;if s.timestamp.ticks()==0{return Err(Error::unexpected("successful sync FILETIME zero"));}
                println!("SYNC_PROBE source={source:?} id={} value={:?} quality={} filetime={} hresult=0",ids[i],s.value,s.quality,s.timestamp.ticks());}
        }
        for (source,tx) in [(OPC_DS_CACHE,101),(OPC_DS_DEVICE,102)]{
            unsafe{io.Refresh2(source,tx)}.map_err(err)?;
            check_samples(&wait_event(&events,"change",tx)?,2)?;
        }
        if group.set_items_active(&[handles[1]],false)?.iter().any(|r|r.is_err()){return Err(Error::unexpected("inactive-item setup failed"));}
        unsafe{io.Refresh2(OPC_DS_CACHE,103)}.map_err(err)?;
        check_samples(&wait_event(&events,"change",103)?,1)?;
        let raw:Vec<u32>=handles.iter().map(|h|h.raw()).collect();
        let mut cancel=0;let mut errors=std::ptr::null_mut();
        unsafe{io.Read(2,raw.as_ptr(),201,&mut cancel,&mut errors)}.map_err(err)?;
        if cancel==0||errors.is_null(){return Err(Error::unexpected("invalid async request outputs"));}
        let accepted=unsafe{std::slice::from_raw_parts(errors,2)}.iter().all(|e|e.0==0);
        unsafe{CoTaskMemFree(Some(errors.cast()))};if !accepted{return Err(Error::unexpected("async valid handles rejected"));}
        check_samples(&wait_event(&events,"read",201)?,2)?; // Async Read includes the inactive item.
        if group.set_items_active(&[handles[1]],true)?.iter().any(|r|r.is_err()){return Err(Error::unexpected("active-item restore failed"));}
        let mixed=[raw[0],u32::MAX];errors=std::ptr::null_mut();cancel=0;
        let hr=unsafe{(Interface::vtable(&io).Read)(Interface::as_raw(&io),2,mixed.as_ptr(),202,&mut cancel,&mut errors)};
        if hr.0!=1||errors.is_null(){return Err(Error::unexpected(format!("partial HRESULT expected S_FALSE got {}",hr.0)));}
        let es=unsafe{std::slice::from_raw_parts(errors,2)}.to_vec();unsafe{CoTaskMemFree(Some(errors.cast()))};
        if es[0].0!=0||es[1].0!=0xc0040001u32 as i32{return Err(Error::unexpected("partial per-item HRESULT mismatch"));}
        check_samples(&wait_event(&events,"read",202)?,1)?;
        errors=std::ptr::null_mut();cancel=0;unsafe{io.Read(2,raw.as_ptr(),301,&mut cancel,&mut errors)}.map_err(err)?;
        unsafe{CoTaskMemFree(Some(errors.cast()));io.Cancel2(cancel)}.map_err(err)?;
        wait_event(&events,"cancel",301)?;
        if events.lock().unwrap().iter().any(|e|e["method"]=="read"&&e["transaction"]==301){return Err(Error::unexpected("cancelled read completed"));}
        unsafe{io.SetEnable(true)}.map_err(err)?;
        check_samples(&wait_event(&events,"change",0)?,2)?;
        if !std::env::args().any(|a|a=="--async-project-client-test") {
            std::thread::sleep(Duration::from_millis(700));
            let n=events.lock().unwrap().iter().filter(|e|e["method"]=="change"&&e["transaction"]==0).count();
            if n<2{return Err(Error::unexpected("no successive Advise change events"));}
            let flag=std::env::current_exe().map_err(|e|Error::unexpected(e.to_string()))?.with_file_name("rust-da-source-offline.flag");
            std::fs::OpenOptions::new().write(true).create_new(true).open(&flag).map_err(|e|Error::unexpected(e.to_string()))?;
            let cleanup=OwnFlag(flag.clone());
            let before=events.lock().unwrap().len();wait_quality(&events,before,false)?;
            let a=group.read(DataSource::Cache,&handles)?.into_iter().map(|r|r.map_err(|e|Error::unexpected(e.to_string()))).collect::<Result<Vec<_>>>()?;
            std::thread::sleep(Duration::from_millis(350));
            let b=group.read(DataSource::Cache,&handles)?.into_iter().map(|r|r.map_err(|e|Error::unexpected(e.to_string()))).collect::<Result<Vec<_>>>()?;
            if a.iter().zip(&b).any(|(a,b)|a.quality&192!=0||a.timestamp.ticks()!=b.timestamp.ticks()){return Err(Error::unexpected("offline state fabricated timestamp/Good"));}
            let before=events.lock().unwrap().len();drop(cleanup);wait_quality(&events,before,true)?;
        }
        let mut revised=0;
        unsafe{group_io.SetState(std::ptr::null(),&mut revised,&windows_core::BOOL(0),std::ptr::null(),std::ptr::null(),std::ptr::null(),std::ptr::null())}.map_err(err)?;
        std::thread::sleep(Duration::from_millis(300));let before=events.lock().unwrap().len();
        std::thread::sleep(Duration::from_millis(400));
        if events.lock().unwrap().len()!=before{return Err(Error::unexpected("inactive group still notified"));}
        if unsafe{io.Refresh2(OPC_DS_CACHE,402)}.is_ok(){return Err(Error::unexpected("Refresh2 accepted inactive group"));}
        unsafe{group_io.SetState(std::ptr::null(),&mut revised,&windows_core::BOOL(1),std::ptr::null(),std::ptr::null(),std::ptr::null(),std::ptr::null())}.map_err(err)?;
        unsafe{io.SetEnable(false)}.map_err(err)?;
        std::thread::sleep(Duration::from_millis(250));let n=events.lock().unwrap().len();
        std::thread::sleep(Duration::from_millis(500));if events.lock().unwrap().len()!=n{return Err(Error::unexpected("SetEnable(false) did not suppress unsolicited events"));}
        unsafe{io.Refresh2(OPC_DS_CACHE,401)}.map_err(err)?;check_samples(&wait_event(&events,"change",401)?,2)?;
        println!("ASYNC_COM_INTERPROCESS_PASS sync_cache_device=true refresh_cache_device=true async_read=true partial_errors=true cancel=true advise=true active_items_group=true enable_disable=true loss_recovery_fixture={}", !std::env::args().any(|a|a=="--async-project-client-test"));
        Ok(())
    })();
    let _=unsafe{point.Unadvise(cookie)};
    let _=group.close(true);
    result
}

pub fn kepware_timestamp_check()->Result<()> {
    if std::env::var("COMPUTERNAME").unwrap_or_default().to_ascii_uppercase()!="LB3PI1" {
        return Err(Error::invalid_argument("timestamp probe is restricted to LB3PI1"));
    }
    let apartment=ComApartment::mta()?;
    let id=Guid::new(0x6e6170f0,0xff2d,0x11d2,[0x80,0x87,0,0x10,0x5a,0xa8,0xf8,0x40]);
    let client=DaClient::connect_class_id(&apartment,&id,opc_classic_types::ClassContext::LOCAL_SERVER)?;
    let group=client.add_group(GroupOptions::new("RustKepwareReadOnlyTimestampProbe"))?;
    let ids=["Channel_0_User_Defined.Ramp.Ramp_Float","Channel_0_User_Defined.User.User2"];
    let added=group.add_items(&[ItemSpec::new(ids[0],601),ItemSpec::new(ids[1],602)])?;
    let handles=added.into_iter().map(|r|r.map(|i|i.server_handle).map_err(|e|Error::unexpected(e.to_string()))).collect::<Result<Vec<_>>>()?;
    for (id,r) in ids.iter().zip(group.read(DataSource::Device,&handles)?) {
        let s=r.map_err(|e|Error::unexpected(e.to_string()))?;
        println!("KEPWARE_NATIVE_SAMPLE id={id} value={:?} quality={} filetime={} hresult=0 method=IOPCSyncIO::Read",s.value,s.quality,s.timestamp.ticks());
    }
    group.close(true)?;
    println!("KEPWARE_NATIVE_TIMESTAMP_PASS read_only=true");Ok(())
}
