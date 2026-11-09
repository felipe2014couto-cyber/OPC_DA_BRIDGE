# SPDX-License-Identifier: MIT
# Synthetic telemetry + real COM in one logon session. No Registry or OPC writes.
param([string]$LaboratoryDirectory,[string]$ExpectedSha256)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Web.Extensions
$json=New-Object Web.Script.Serialization.JavaScriptSerializer
$json.MaxJsonLength=4194304
$utf8=New-Object Text.UTF8Encoding($false)
$exe=Join-Path $LaboratoryDirectory 'rust-da-com-lab.exe'
$projectPath=Join-Path $LaboratoryDirectory 'project.json'
$project=$json.DeserializeObject([IO.File]::ReadAllText($projectPath,$utf8))
if($project['deployment']['machine_id'] -ne 'discovery-lab-destination' -or $project['diagnostic_port'] -ne 49326){throw 'Not the isolated synthetic fixture'}
$hash=[BitConverter]::ToString(([Security.Cryptography.SHA256]::Create()).ComputeHash([IO.File]::ReadAllBytes($exe))).Replace('-','').ToLowerInvariant()
if($hash -ne $ExpectedSha256){throw 'Executable hash mismatch'}
$own=@(Get-WmiObject Win32_Process -Filter "Name='rust-da-com-lab.exe'"|Where-Object {$_.ExecutablePath -eq $exe})
if($own.Count){throw 'Isolated executable already active; refusing to replace/stop it'}
$stop=Join-Path $LaboratoryDirectory 'agent-stop.flag'
if(Test-Path $stop){Remove-Item -LiteralPath $stop}
function Encode($v){
 if($null -eq $v){return 'null'};if($v -is [string]){return $json.Serialize([string]$v)}
 if($v -is [bool]){if($v){return 'true'}else{return 'false'}}
 if($v -is [Collections.IDictionary]){$a=@();foreach($k in $v.Keys){$a+=($json.Serialize([string]$k)+':'+(Encode $v[$k]))};return '{'+($a -join ',')+'}'}
 if($v -is [Collections.IEnumerable]){$a=@();foreach($i in $v){$a+=(Encode $i)};return '['+($a -join ',')+']'}
 return [Convert]::ToString($v,[Globalization.CultureInfo]::InvariantCulture)
}
function Snapshot{$w=New-Object Net.WebClient;$w.Proxy=$null;try{return $json.DeserializeObject($w.DownloadString('http://127.0.0.1:49326/v1/snapshot'))}finally{$w.Dispose()}}
function Post($route,$payload){
 $r=[Net.HttpWebRequest]::Create('http://127.0.0.1:49326'+$route);$r.Proxy=$null;$r.Method='POST';$r.ContentType='application/json';$r.Timeout=5000;$r.ReadWriteTimeout=5000
 $r.Headers.Add('Authorization',('Bearer '+$script:token));$b=$utf8.GetBytes((Encode $payload));$r.ContentLength=$b.Length
 $stream=$r.GetRequestStream();try{$stream.Write($b,0,$b.Length)}finally{$stream.Close()}
 try{$resp=$r.GetResponse()}catch [Net.WebException]{if($_.Exception.Response){$code=[int]$_.Exception.Response.StatusCode;$_.Exception.Response.Close();return @{ok=$false;http_status=$code}};throw}
 try{$reader=New-Object IO.StreamReader($resp.GetResponseStream(),$utf8);return $json.DeserializeObject($reader.ReadToEnd())}finally{$resp.Close()}
}
function Header{return @{machine_id='discovery-lab-destination';configuration_revision=1;config_sha256=$script:digest;source_module_id='source-lab'}}
function Batch($value,[long]$sequence){
 $p=Header;$p['session_id']=$script:session;$p['sequence']=$sequence;$p['samples']=@()
 foreach($v in $project['connections'][0]['modules'][0]['variables']){
  $boolean=$v['data_type'] -eq 'Boolean';$val=if($boolean){$false}else{[double]$value};$vt=if($boolean){11}else{5}
  $p['samples']+=@{item_id=$v['item_id'];source_item_id=$v['source']['item_id'];data_type=$v['data_type'];value=$val;quality=192;hresult=0;source_timestamp_filetime=([long]134384447199035278+$sequence);source_vartype=$vt}
 };return $p
}
function NumberSample($snap){return @($snap['samples']|Where-Object {$_['item_id'] -eq 'DiscoveryLab.Number'})[0]}
function SendBatch($value,$sequence){$ack=Post '/v1/source/samples' (Batch $value $sequence);if($ack['ok'] -ne $true -or $ack['pid'] -ne $script:server.Id){throw 'Sample ACK failed'}}
$server=$null
try{
 $nonce=[Guid]::NewGuid().ToString('N')
 $server=Start-Process -FilePath $exe -ArgumentList '--volatile-server --isolated-source-test' -WorkingDirectory $LaboratoryDirectory -PassThru -RedirectStandardOutput (Join-Path $LaboratoryDirectory ('server-'+$nonce+'.out')) -RedirectStandardError (Join-Path $LaboratoryDirectory ('server-'+$nonce+'.err'))
 $snap=$null;for($i=0;$i -lt 40;$i++){Start-Sleep -Milliseconds 200;try{$snap=Snapshot;break}catch{}}
 if(!$snap -or $snap['pid'] -ne $server.Id){throw 'Wrong runtime PID'}
 $token=([IO.File]::ReadAllText((Join-Path $LaboratoryDirectory 'runtime-auth.token'),$utf8)).Trim()
 $digest=[BitConverter]::ToString(([Security.Cryptography.SHA256]::Create()).ComputeHash([IO.File]::ReadAllBytes($projectPath))).Replace('-','').ToLowerInvariant()
 $session=(Post '/v1/source/session' (Header))['session_id'];if(!$session){throw 'No source session'}
 SendBatch 7 1
 $out=Join-Path $LaboratoryDirectory ('client-'+$nonce+'.out');$err=Join-Path $LaboratoryDirectory ('client-'+$nonce+'.err')
 $client=Start-Process -FilePath $exe -ArgumentList '--project-client-test --isolated-source-test --read-only-project-client --project-client-pause-ms 5000' -WorkingDirectory $LaboratoryDirectory -PassThru -RedirectStandardOutput $out -RedirectStandardError $err
 $clientHandle=$client.Handle
 Start-Sleep -Seconds 2;SendBatch 42 2
 if(!$client.WaitForExit(20000)){throw 'COM client timeout; process left for inspection'}
 $text=[IO.File]::ReadAllText($out,$utf8);$text
 if($client.ExitCode -ne 0 -or $text -notmatch 'PROJECT_COM_CLIENT_PASS' -or $text -notmatch 'source=Cache value=F64\(7.0\)' -or $text -notmatch 'source=Device value=F64\(42.0\)'){Get-Content $err;throw 'Connected COM update failed'}
 foreach($bad in @('revision','identity','replay','type')){
  $p=Batch 99 3
  if($bad -eq 'revision'){$p['configuration_revision']=0};if($bad -eq 'identity'){$p['machine_id']='other-destination'}
  if($bad -eq 'replay'){$p['sequence']=1};if($bad -eq 'type'){$p['samples'][0]['value']=$true}
  if((Post '/v1/source/samples' $p)['ok'] -eq $true){throw ('Invalid packet accepted '+$bad)}
 }
 $before=NumberSample (Snapshot);if($before['value'] -ne 42){throw 'Rejected update changed runtime'}
 $bad=Batch 0 3;$bad['samples'][0]['value']=$null;$bad['samples'][0]['hresult']=-1073479673;$bad['samples'][0]['quality']=0;$bad['samples'][0]['source_timestamp_filetime']=$null
 if((Post '/v1/source/samples' $bad)['ok'] -ne $true){throw 'Item error not acknowledged'}
 $s=NumberSample (Snapshot)
 if($s['value'] -ne 42 -or ($s['quality'] -band 192) -ne 0 -or $s['hresult'] -ge 0 -or $s['source_timestamp_filetime'] -ne $before['source_timestamp_filetime']){throw 'Last known state invalid'}
 SendBatch 8 4;$before=NumberSample (Snapshot);Start-Sleep -Seconds 16;$s=NumberSample (Snapshot)
 if($s['quality'] -ne 8 -or $s['hresult'] -ge 0 -or $s['value'] -ne 8 -or $s['source_timestamp_filetime'] -ne $before['source_timestamp_filetime']){throw 'Source expiry failed'}
 SendBatch 9 5;$s=NumberSample (Snapshot)
 if($s['quality'] -ne 192 -or (Snapshot)['pid'] -ne $server.Id){throw 'Recovery/PID check failed'}
 'SOURCE_RUNTIME_WINDOWS_PASS same_pid='+$server.Id+' cache=7 device=42 vqt_hresult expiry recovery invalid_type identity revision replay write=not_called'
}finally{
 if($server){$check=Get-WmiObject Win32_Process -Filter ('ProcessId='+$server.Id)
  if($check -and $check.ExecutablePath -eq $exe -and $check.CommandLine -match '--isolated-source-test'){
   [IO.File]::WriteAllText($stop,'stop',$utf8);if(!$server.WaitForExit(10000)){Write-Warning 'Isolated server did not stop normally; not forcing termination'}
  }
 }
}
