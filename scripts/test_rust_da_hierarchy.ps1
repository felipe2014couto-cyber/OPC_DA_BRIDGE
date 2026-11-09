# SPDX-License-Identifier: MIT
# Isolated Windows 7 x86 COM regression. No registration, OPC writes or services.
param([Parameter(Mandatory=$true)][string]$LaboratoryDirectory,
      [Parameter(Mandatory=$true)][string]$ExpectedSha256)
$ErrorActionPreference='Stop'
$dir=[IO.Path]::GetFullPath($LaboratoryDirectory)
if($dir -notmatch '^D:\\OPCB\\rust-da-hierarchy-test-[a-zA-Z0-9-]+$'){throw 'Not an isolated hierarchy test folder'}
$exe=Join-Path $dir 'rust-da-com-lab.exe';$projectPath=Join-Path $dir 'project.json';$flag=Join-Path $dir 'agent-stop.flag'
$sha=[Security.Cryptography.SHA256]::Create()
$hash=[BitConverter]::ToString($sha.ComputeHash([IO.File]::ReadAllBytes($exe))).Replace('-','').ToLowerInvariant()
if($hash -ne $ExpectedSha256){throw 'Test executable SHA256 mismatch'}
if(Test-Path $flag){throw 'Existing shutdown marker; refusing test'}
Add-Type -AssemblyName System.Web.Extensions
$json=New-Object System.Web.Script.Serialization.JavaScriptSerializer
$project=$json.DeserializeObject([IO.File]::ReadAllText($projectPath,[Text.Encoding]::UTF8))
if($project['diagnostic_port'] -ne 49326 -or $project['deployment']['machine_id'] -ne 'hierarchy-isolated-lab'){throw 'Wrong test identity/port'}
foreach($c in $project['connections']){foreach($m in $c['modules']){foreach($v in $m['variables']){if($v['source']){throw 'Test must use only its simulated source'}}}}
if(@([Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners()|Where-Object {$_.Port -eq 49326}).Count){throw 'Isolated test port occupied'}
$server=$null;$client=$null;$nonce=[Guid]::NewGuid().ToString('N')
try {
 & $exe --self-test
 if($LASTEXITCODE -ne 0){throw 'Legacy COM regression failed'}
 & $exe --browse-self-test
 if($LASTEXITCODE -ne 0){throw 'Legacy flat/enumerator regression failed'}
 $argsServer='--volatile-server --isolated-source-test --project "'+$projectPath+'"'
 $server=Start-Process -FilePath $exe -ArgumentList $argsServer -WorkingDirectory $dir -PassThru -RedirectStandardOutput (Join-Path $dir ('server-'+$nonce+'.out')) -RedirectStandardError (Join-Path $dir ('server-'+$nonce+'.err'))
 $snap=$null
 for($i=0;$i -lt 40;$i++){Start-Sleep -Milliseconds 200;try{$w=New-Object Net.WebClient;$w.Proxy=$null;try{$snap=$json.DeserializeObject($w.DownloadString('http://127.0.0.1:49326/v1/snapshot'))}finally{$w.Dispose()};break}catch{}}
 if(!$snap -or $snap['pid'] -ne $server.Id){throw 'Isolated runtime PID mismatch'}
 $out=Join-Path $dir ('client-'+$nonce+'.out');$err=Join-Path $dir ('client-'+$nonce+'.err')
 $argsClient='--project-client-test --read-only-project-client --isolated-source-test --project "'+$projectPath+'"'
 $client=Start-Process -FilePath $exe -ArgumentList $argsClient -WorkingDirectory $dir -PassThru -RedirectStandardOutput $out -RedirectStandardError $err
 $handle=$client.Handle
 if(!$client.WaitForExit(20000)){throw 'Isolated COM client timed out'}
 $text=[IO.File]::ReadAllText($out,[Text.Encoding]::UTF8);[Console]::Out.WriteLine($text)
 if($client.ExitCode -ne 0 -or $text -notmatch 'PROJECT_HIERARCHY_PASS' -or $text -notmatch 'PROJECT_COM_CLIENT_PASS'){
  [Console]::Out.WriteLine([IO.File]::ReadAllText($err,[Text.Encoding]::UTF8));throw 'Cross-process hierarchical browse/read failed'
 }
 if($text -match 'PROJECT_WRITE_REJECTION'){throw 'Unexpected write test was invoked'}
 Write-Output ('HIERARCHY_WINDOWS_COM_PASS server_pid='+$server.Id+' client_pid='+$client.Id+' persistent_registration=false writes=not_called')
} finally {
 if($client){$p=Get-WmiObject Win32_Process -Filter ('ProcessId='+$client.Id);if($p -and $p.ExecutablePath -ieq $exe -and $p.CommandLine -match '--project-client-test'){Stop-Process -Id $client.Id}}
 if($server){$p=Get-WmiObject Win32_Process -Filter ('ProcessId='+$server.Id);if($p -and $p.ExecutablePath -ieq $exe -and $p.CommandLine -match '--isolated-source-test'){
  $f=[IO.File]::Open($flag,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None);$f.Close()
  if(!$server.WaitForExit(10000)){throw 'Isolated server did not stop normally'}
  Write-Output ('HIERARCHY_TEST_NORMAL_STOP pid='+$server.Id)
 }}
}
