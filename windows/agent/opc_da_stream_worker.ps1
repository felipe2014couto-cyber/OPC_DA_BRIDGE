# SPDX-License-Identifier: MIT
# Private child of opc-telemetry-agent --source-directory. No service/COM setup.
# File IPC avoids the Windows PowerShell 2 stdin host behavior. The native
# parent bounds every request and owns/terminates only this child on timeout.
param([string]$SessionDirectory)
$boot=[Diagnostics.Stopwatch]::StartNew()
$ErrorActionPreference='Stop'
$workerRoot=Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $workerRoot 'opc_da_source_agent.ps1') -LibraryOnly
$script:keep_source_session=$true
$script:source_session=$null
$bindingText=[IO.File]::ReadAllText((Join-Path $workerRoot 'source-identity.json'),$utf8)
$binding=$json.DeserializeObject($bindingText)
$identity=Identity
$script:timestamp_policy=[string]$binding['timestamp_policy']
$requestPath=Join-Path $SessionDirectory 'request.json'
$responsePath=Join-Path $SessionDirectory 'response.json'
$stopPath=Join-Path $SessionDirectory 'stop.flag'
$catalog=$null
$age=[Diagnostics.Stopwatch]::StartNew()
$bootMs=$boot.Elapsed.TotalMilliseconds
try{
 if($identity['hostname'] -ne $binding['expected_hostname'] -or !($identity['addresses'] -contains $binding['expected_address'])){throw 'identity_mismatch'}
 if(!(@('reject_unspecified','utc','local') -contains $script:timestamp_policy)){throw 'invalid_timestamp_policy'}
 while($age.Elapsed.TotalSeconds -lt 305 -and !(Test-Path $stopPath)){
  if(!(Test-Path $requestPath)){Start-Sleep -Milliseconds 5;continue}
  $timer=[Diagnostics.Stopwatch]::StartNew();$reply=$null
  try{
   if([IO.File]::ReadAllText((Join-Path $workerRoot 'source-identity.json'),$utf8) -cne $bindingText){throw 'binding_changed'}
   if((Get-Item $requestPath).Length -gt 65536){throw 'request_too_large'}
   $requestText=[IO.File]::ReadAllText($requestPath,$utf8);$request=$json.DeserializeObject($requestText)
   if($catalog -and $catalog -cne $requestText){throw 'stream_catalog_changed'}
   if($request['operation'] -ne 'read' -or $request['transport'] -ne 'opc_da_automation'){throw 'operation_not_allowed'}
   if(!(@($binding['allowed_progids']) -contains $request['prog_id'])){throw 'progid_not_authorized'}
   $key=[Microsoft.Win32.Registry]::ClassesRoot.OpenSubKey($request['prog_id']+'\CLSID')
   if(!$key){throw 'progid_not_registered'}
   try{if([string]$key.GetValue('') -ne $request['clsid']){throw 'clsid_mismatch'}}finally{$key.Close()}
   if(@($request['items']).Count -lt 1 -or @($request['items']).Count -gt 128){throw 'invalid_item_count'}
   foreach($entry in $request['items']){if(!(@($binding['stream_allowed_items']) -contains $entry['source_item_id'])){throw 'item_not_authorized'}}
   $reused=($null -ne $script:source_session)
   $samples=@(ReadItems $request)
   $reply=@{ok=$true;identity=$identity;transport='opc_da_automation';read_method='OPCGroup.SyncRead';agent_revision='persistent-source-v1';source_pid=$PID;source_timing_ms=@{worker_initialization=$bootMs;com_initialization=$script:source_init_ms;add_items=$script:source_add_ms;opc_read=$script:source_read_ms;read_and_conversion=$timer.Elapsed.TotalMilliseconds;session_reused=$reused};samples=$samples}
   $catalog=$requestText
   if(@($samples|Where-Object {$_['hresult'] -lt 0}).Count){ClosePersistentSource}
  }catch{
   $reply=@{ok=$false;error_code='source_error';hresult=(ErrorCode $_.Exception);diagnostic_stage=$script:com_phase}
   ClosePersistentSource
  }
  [IO.File]::WriteAllText(($responsePath+'.tmp'),(Encode $reply),$utf8)
  [IO.File]::Delete($requestPath)
  [IO.File]::Move(($responsePath+'.tmp'),$responsePath)
 }
}finally{ClosePersistentSource}
