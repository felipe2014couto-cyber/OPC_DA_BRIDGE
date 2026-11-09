# SPDX-License-Identifier: MIT
# Fixed-purpose internal lab agent. Uses existing Windows PowerShell/.NET runtime.
param([ValidateSet('identify','snapshot','apply','telemetry')][string]$Operation='identify',[string]$InboxName='',[switch]$RequestStdin,[string]$RequestJson='')
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false)
Add-Type -AssemblyName System.Web.Extensions
$json=New-Object System.Web.Script.Serialization.JavaScriptSerializer
$json.MaxJsonLength=4194304
$root=Split-Path -Parent $MyInvocation.MyCommand.Path
$server=Join-Path $root 'rust-da-com-lab.exe'
$active=Join-Path $root 'project.json'
$receiptPath=Join-Path $root 'receipt.json'
$stopFile=Join-Path $root 'agent-stop.flag'
$utf8=New-Object Text.UTF8Encoding($false)
function ReadJson($p){return $json.DeserializeObject([IO.File]::ReadAllText($p,[Text.Encoding]::UTF8))}
# PowerShell 2 adapted objects cannot be serialized directly by JavaScriptSerializer.
# Keep its string escaping but emit only explicit JSON primitives/containers.
function JsonEncode($v){
 if($null -eq $v){return 'null'}
 if($v -is [string]){return $json.Serialize([string]$v)}
 if($v -is [bool]){if($v){return 'true'}else{return 'false'}}
 if($v -is [Collections.IDictionary]){$parts=@();foreach($k in $v.Keys){$parts+=($json.Serialize([string]$k)+':'+(JsonEncode $v[$k]))};return '{'+($parts -join ',')+'}'}
 if($v -is [Collections.IEnumerable]){$parts=@();foreach($item in $v){$parts+=(JsonEncode $item)};return '['+($parts -join ',')+']'}
 if($v -is [ValueType]){return [Convert]::ToString($v,[Globalization.CultureInfo]::InvariantCulture)}
 throw 'Unsupported agent JSON value'
}
function Digest($bytes){return [BitConverter]::ToString(([Security.Cryptography.SHA256]::Create()).ComputeHash($bytes)).Replace('-','').ToLowerInvariant()}
function DigestFile($path){$stream=[IO.File]::OpenRead($path);$sha=[Security.Cryptography.SHA256]::Create();try{return [BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-','').ToLowerInvariant()}finally{$sha.Clear();$stream.Close()}}
function Atomic($p,$text){
 $nonce=[Guid]::NewGuid().ToString('N')
 $tmp=$p+'.tmp-'+$nonce
 $backup=$p+'.replace-backup-'+$nonce
 [IO.File]::WriteAllText($tmp,$text,$utf8)
 # PowerShell 2 converts a null string argument to an empty path. A real,
 # unique backup path keeps File.Replace atomic on the .NET 2 runtime.
 $replaced=$false
 try{if(Test-Path $p){[IO.File]::Replace($tmp,$p,$backup)}else{[IO.File]::Move($tmp,$p)};$replaced=$true}finally{
  if(Test-Path $tmp){Remove-Item -LiteralPath $tmp -ErrorAction SilentlyContinue}
  # Keep any backup on failure for diagnosis and recovery.
  if($replaced -and (Test-Path $backup)){Remove-Item -LiteralPath $backup -ErrorAction SilentlyContinue}
 }
}
function Log($message){[IO.File]::AppendAllText((Join-Path $root 'agent.log'),([DateTime]::UtcNow.ToString('o')+' '+$message+"`r`n"),$utf8)}
function Identity {
 $ips=@(Get-WmiObject Win32_NetworkAdapterConfiguration|Where-Object {$_.IPEnabled}|ForEach-Object {$_.IPAddress}|Where-Object {$_ -match '^\d+\.\d+\.\d+\.\d+$'})
 return @{hostname=([string](Get-WmiObject Win32_ComputerSystem).Name);addresses=$ips;architecture=([IntPtr]::Size*8).ToString()+'-bit';os_version=[Environment]::OSVersion.VersionString}
}
function Snapshot {
 $request=[Net.HttpWebRequest]::Create('http://127.0.0.1:'+$binding['diagnostic_port']+'/v1/snapshot');$request.Proxy=$null;$request.Timeout=1500;$request.ReadWriteTimeout=1500
 $response=$request.GetResponse()
 try{$reader=New-Object IO.StreamReader($response.GetResponseStream(),[Text.Encoding]::UTF8);try{$text=$reader.ReadToEnd()}finally{$reader.Close()}}finally{$response.Close()}
 if($text.Length -gt 2097152){throw 'Oversized runtime response'}
 return $json.DeserializeObject($text)
}
function StopOwn {
 $own=@(Get-WmiObject Win32_Process -Filter "Name='rust-da-com-lab.exe'"|Where-Object {$_.ExecutablePath -eq $server})
 if($own.Count -gt 1){throw 'Multiple laboratory instances; close clients before apply'}
 if(!$own.Count){return}
 $target=$own[0];Log ('STOP_REQUEST pid='+$target.ProcessId+' verified_path='+$server)
 [IO.File]::WriteAllText($stopFile,'stop',$utf8)
 for($i=0;$i -lt 60;$i++){Start-Sleep -Milliseconds 200;if(!(Get-Process -Id $target.ProcessId -ErrorAction SilentlyContinue)){return}}
 throw 'Laboratory server did not stop normally; no forced process termination performed'
}
function StructuralDiff($a,$b){
 if(@($a['connections']).Count -ne @($b['connections']).Count){return $true}
 for($ci=0;$ci -lt @($a['connections']).Count;$ci++){
  $ca=$a['connections'][$ci];$cb=$b['connections'][$ci]
  if($ca['name'] -ne $cb['name']){return $true}
  if(@($ca['modules']).Count -ne @($cb['modules']).Count){return $true}
  for($mi=0;$mi -lt @($ca['modules']).Count;$mi++){
   $ma=$ca['modules'][$mi];$mb=$cb['modules'][$mi]
   if($ma['name'] -ne $mb['name']){return $true}
   if(@($ma['variables']).Count -ne @($mb['variables']).Count){return $true}
   for($vi=0;$vi -lt @($ma['variables']).Count;$vi++){
    $va=$ma['variables'][$vi];$vb=$mb['variables'][$vi]
    $ida=if($va['item_id']){$va['item_id']}else{$ma['name']+'.'+$va['name']}
    $idb=if($vb['item_id']){$vb['item_id']}else{$mb['name']+'.'+$vb['name']}
    if($va['name'] -ne $vb['name'] -or $va['data_type'] -ne $vb['data_type'] -or $ida -ne $idb){return $true}
   }
  }
 }
 return $false
}
function ValidateRuntime($snap,$envelope,$project){
 if($snap['config_sha256'] -ne $envelope['project_sha256'] -or $snap['machine_id'] -ne $binding['machine_id'] -or [long]$snap['configuration_revision'] -ne [long]$envelope['configuration_revision']){throw 'Runtime digest or deployment identity mismatch'}
 $variables=@();foreach($c in $project['connections']){foreach($m in $c['modules']){$variables+=@($m['variables'])}}
 if(@($snap['samples']).Count -ne $variables.Count){throw 'Runtime catalog count mismatch'}
 foreach($v in $variables){$s=@($snap['samples']|Where-Object {$_['item_id'] -eq $v['item_id']});if($s.Count -ne 1 -or $s[0]['data_type'] -ne $v['data_type'] -or (!$v['source'] -and ($s[0]['value'] -ne $v['initial_value'] -or [int]$s[0]['quality'] -ne 192 -or !$s[0]['source_timestamp_filetime']))){throw 'Runtime item/value/type/quality/timestamp mismatch'}}
}
$lock=$null;$candidate=$null
try {
 if($RequestStdin -and ($Operation -ne 'telemetry' -or $InboxName)){throw 'Stdin is limited to telemetry'}
 if(!$RequestStdin -and $RequestJson){throw 'Inline JSON requires telemetry stdin mode'}
 $binding=ReadJson (Join-Path $root 'identity.json')
 $identity=Identity
 if($identity['hostname'] -ne $binding['expected_hostname'] -or !($identity['addresses'] -contains $binding['expected_address'])){throw 'Provisioned identity does not match this Windows host'}
 $receipt=$null;if(Test-Path $receiptPath){$receipt=ReadJson $receiptPath}
 if($Operation -eq 'identify'){
  $snapshot=$null;try{$snapshot=Snapshot}catch{}
  [Console]::Out.WriteLine((JsonEncode @{ok=$true;identity=$identity;machine_id=$binding['machine_id'];receipt=$receipt;snapshot=$snapshot;agent_version='1.0-lab'}));exit 0
 }
 if($Operation -eq 'snapshot'){
  $snapshot=Snapshot
  if(!$receipt -or $snapshot['config_sha256'] -ne $receipt['project_sha256'] -or $snapshot['machine_id'] -ne $binding['machine_id']){throw 'No confirmed matching runtime configuration'}
  [Console]::Out.WriteLine((JsonEncode @{ok=$true;identity=$identity;machine_id=$binding['machine_id'];receipt=$receipt;snapshot=$snapshot}));exit 0
 }
 if($RequestStdin -and ($Operation -ne 'telemetry' -or $InboxName)){throw 'Stdin is limited to telemetry'}
 if(!$RequestStdin -and $InboxName -notmatch '^[0-9a-f-]{36}\.json$'){throw 'Invalid inbox document name'}
 $lock=[IO.File]::Open((Join-Path $root 'apply.lock'),[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None)
 if($RequestStdin){
  if(!$RequestJson -or $RequestJson.Length -gt 2097152){throw 'Invalid or oversized telemetry document'}
  $e=$json.DeserializeObject($RequestJson)
  if(!$e){throw 'Invalid telemetry document'}
 }else{
  $payload=Join-Path (Join-Path $root 'inbox') $InboxName
  if((Get-Item $payload).Length -gt 2097152){throw 'Oversized deployment document'}
  $e=ReadJson $payload
 }
 if($e['schema_version'] -ne 1 -or $e['deployment_id'] -notmatch '^[0-9a-f-]{36}$' -or (!$RequestStdin -and $e['deployment_id']+'.json' -ne $InboxName) -or $e['machine_id'] -ne $binding['machine_id'] -or $e['expected_hostname'] -ne $identity['hostname'] -or $e['expected_address'] -ne $binding['expected_address']){throw 'Deployment target identity mismatch'}
 if($Operation -eq 'telemetry'){
  $route=if($e['operation'] -eq 'source-session'){'/v1/source/session'}elseif($e['operation'] -eq 'source-samples'){'/v1/source/samples'}else{throw 'Invalid telemetry operation'}
  $own=@(Get-WmiObject Win32_Process -Filter "Name='rust-da-com-lab.exe'"|Where-Object {$_.ExecutablePath -eq $server})
  if($own.Count -ne 1){throw 'Laboratory runtime not available'}
  $token=([IO.File]::ReadAllText((Join-Path $root 'runtime-auth.token'),$utf8)).Trim()
  $req=[Net.HttpWebRequest]::Create('http://127.0.0.1:'+$binding['diagnostic_port']+$route)
  $req.Method='POST';$req.ContentType='application/json';$req.Proxy=$null;$req.Timeout=5000;$req.ReadWriteTimeout=5000
  $req.Headers.Add('Authorization',('Bearer '+$token));$bytes=$utf8.GetBytes((JsonEncode $e));$req.ContentLength=$bytes.Length
  $stream=$req.GetRequestStream();try{$stream.Write($bytes,0,$bytes.Length)}finally{$stream.Close()}
  $response=$req.GetResponse();try{$reader=New-Object IO.StreamReader($response.GetResponseStream(),$utf8);$reply=$json.DeserializeObject($reader.ReadToEnd())}finally{$response.Close()}
  if($reply['ok'] -ne $true -or [int]$reply['pid'] -ne [int]$own[0].ProcessId){throw 'Runtime telemetry acknowledgement mismatch'}
  [Console]::Out.WriteLine((JsonEncode $reply));exit 0
 }
 if((Digest ($utf8.GetBytes($e['project_json']))) -ne $e['project_sha256']){throw 'Deployment SHA256 mismatch'}
 $project=$json.DeserializeObject($e['project_json'])
 if($project['deployment']['machine_id'] -ne $binding['machine_id'] -or [long]$project['deployment']['configuration_revision'] -ne [long]$e['configuration_revision'] -or [int]$project['diagnostic_port'] -ne [int]$binding['diagnostic_port']){throw 'Project target metadata mismatch'}
 $candidate=Join-Path $root ('candidate-'+$InboxName)
 [IO.File]::WriteAllText($candidate,$e['project_json'],$utf8)
 $validation=@(& $server --validate-project --project $candidate)
 if($LASTEXITCODE -ne 0){throw 'Rust project validator refused the configuration'}
 $validated=$json.DeserializeObject(($validation -join "`n"))
 if($validated['project_sha256'] -ne $e['project_sha256']){throw 'Rust validator hash mismatch'}
 foreach($scope in @('CurrentUser','LocalMachine')){
  $base=if($scope -eq 'CurrentUser'){[Microsoft.Win32.Registry]::CurrentUser}else{[Microsoft.Win32.Registry]::LocalMachine}
  $key=$base.OpenSubKey('Software\Classes\CLSID\{7A2F45B1-64D3-4E8C-9A11-3C852B7F2140}\LocalServer32')
  if(!$key){throw 'Laboratory COM registration missing'}
  try{if(([string]$key.GetValue('')).Trim('"') -ne $server){throw 'COM registration points to another build'}}finally{$key.Close()}
 }
 $oldText=$null;if(Test-Path $active){$oldText=[IO.File]::ReadAllText($active,[Text.Encoding]::UTF8)}
 $own=@(Get-WmiObject Win32_Process -Filter "Name='rust-da-com-lab.exe'"|Where-Object {$_.ExecutablePath -eq $server})
 if($own.Count -gt 1){throw 'Multiple laboratory instances; close clients before apply'}
 if($own.Count -eq 1){
  # Hot-update path: Server is running and holding COM clients/handles.
  # DO NOT stop or restart the server process.
  if($oldText -ne $null){
   $oldProj=$json.DeserializeObject($oldText)
   if(StructuralDiff $oldProj $project){throw 'Alteração estrutural: reinício exigido.'}
  }
  $tokenPath=Join-Path $root 'runtime-auth.token'
  $token=''
  if(Test-Path $tokenPath){$token=([IO.File]::ReadAllText($tokenPath,[Text.Encoding]::UTF8)).Trim()}
  if(!$token){throw 'Runtime authentication token missing on loopback'}
  $req=[Net.HttpWebRequest]::Create('http://127.0.0.1:'+$binding['diagnostic_port']+'/v1/update')
  $req.Method='POST';$req.ContentType='application/json; charset=utf-8';$req.Proxy=$null;$req.Timeout=5000;$req.ReadWriteTimeout=5000
  $req.Headers.Add('Authorization',('Bearer '+$token))
  $bodyBytes=$utf8.GetBytes((JsonEncode @{project_json=$e['project_json'];project_sha256=$e['project_sha256']}))
  $req.ContentLength=$bodyBytes.Length
  $os=$req.GetRequestStream()
  try{$os.Write($bodyBytes,0,$bodyBytes.Length);$os.Flush()}finally{$os.Close()}
  $ackText=$null
  try{
   $resp=$req.GetResponse()
   try{$sr=New-Object IO.StreamReader($resp.GetResponseStream(),$utf8);$ackText=$sr.ReadToEnd()}finally{$resp.Close()}
  }catch [Net.WebException]{
   $we=$_.Exception
   if($we.Response){
    try{$sr=New-Object IO.StreamReader($we.Response.GetResponseStream(),$utf8);$errText=$sr.ReadToEnd()}finally{$we.Response.Close()}
    try{
     $errObj=$json.DeserializeObject($errText)
     if($errObj['error']){throw $errObj['error']}
    }catch [string]{throw $_}catch{}
   }
   throw $we.Message
  }
  $ack=$json.DeserializeObject($ackText)
  if(!$ack -or $ack['ok'] -ne $true){throw 'Server refused update'}
  $snap=$ack['snapshot']
  ValidateRuntime $snap $e $project
  if($oldText -ne $null){Atomic (Join-Path $root 'last-good-project.json') $oldText}
  $receipt=@{machine_id=$binding['machine_id'];configuration_revision=$e['configuration_revision'];project_sha256=$e['project_sha256'];hostname=$identity['hostname'];address=$binding['expected_address'];variable_count=@($snap['samples']).Count;process_id=$own[0].ProcessId;applied_unix_ms=[long](([DateTime]::UtcNow.Ticks-621355968000000000)/10000);server_executable_sha256=(DigestFile $server);runtime_state='hot_applied_server_preserved'}
  Atomic $receiptPath (JsonEncode $receipt)
  Log ('HOT_APPLIED machine='+$binding['machine_id']+' revision='+$e['configuration_revision']+' digest='+$e['project_sha256']+' pid='+$own[0].ProcessId)
  [Console]::Out.WriteLine((JsonEncode @{ok=$true;identity=$identity;machine_id=$binding['machine_id'];receipt=$receipt;snapshot=$snap}));exit 0
 } else {
  # Cold path: no active instance; validated structural changes may be staged.
  if(Test-Path $stopFile){Remove-Item -LiteralPath $stopFile}
  if($oldText -ne $null){Atomic (Join-Path $root 'last-good-project.json') $oldText}
  $started=$null
  try {
   Atomic $active $e['project_json']
   $started=Start-Process -FilePath $server -ArgumentList '--volatile-server' -WorkingDirectory $root -WindowStyle Hidden -PassThru
   $snap=$null
   for($i=0;$i -lt 60;$i++){Start-Sleep -Milliseconds 200;try{$snap=Snapshot;break}catch{}}
   if(!$snap -or [int]$snap['pid'] -ne $started.Id){throw 'New runtime did not confirm its PID'}
   ValidateRuntime $snap $e $project
   $receipt=@{machine_id=$binding['machine_id'];configuration_revision=$e['configuration_revision'];project_sha256=$e['project_sha256'];hostname=$identity['hostname'];address=$binding['expected_address'];variable_count=@($snap['samples']).Count;process_id=$started.Id;applied_unix_ms=[long](([DateTime]::UtcNow.Ticks-621355968000000000)/10000);server_executable_sha256=(DigestFile $server);runtime_state='validated_waiting_client_activation'}
   StopOwn
   Atomic $receiptPath (JsonEncode $receipt)
   Log ('APPLIED machine='+$binding['machine_id']+' revision='+$e['configuration_revision']+' digest='+$e['project_sha256'])
   [Console]::Out.WriteLine((JsonEncode @{ok=$true;identity=$identity;machine_id=$binding['machine_id'];receipt=$receipt;snapshot=$snap}));exit 0
  } catch {
   $failure=$_.Exception.Message
   try{StopOwn}catch{}
   if($oldText -ne $null){Atomic $active $oldText}else{if(Test-Path $active){Remove-Item -LiteralPath $active}}
   Log 'APPLY_FAILED previous configuration restored'
   throw $failure
  }
 }
} catch {
 Log ('ERROR operation='+$Operation+' '+$_.Exception.Message)
 $code=if($_.Exception.Message -eq 'Laboratory runtime not available'){'runtime_not_running'}else{'agent_error'}
 [Console]::Out.WriteLine((JsonEncode @{ok=$false;error=$_.Exception.Message;error_code=$code}));exit 1
} finally {
 if($candidate -and (Test-Path $candidate)){Remove-Item -LiteralPath $candidate}
 if($Operation -eq 'telemetry' -and $InboxName -match '^[0-9a-f-]{36}\.json$'){$queued=Join-Path (Join-Path $root 'inbox') $InboxName;if(Test-Path $queued){Remove-Item -LiteralPath $queued}}
 if($lock){$lock.Close()}
}
