# Internal, simulated laboratory only. No persistent COM registration.
param(
 [Parameter(Mandatory=$true)][string]$ServerExecutable,
 [Parameter(Mandatory=$true)][string]$ProjectPath,
 [Parameter(Mandatory=$true)][string]$ExpectedSha256
)
$ErrorActionPreference='Stop'
$ServerExecutable=[IO.Path]::GetFullPath($ServerExecutable)
$ProjectPath=[IO.Path]::GetFullPath($ProjectPath)
$hash=[BitConverter]::ToString(([Security.Cryptography.SHA256]::Create()).ComputeHash([IO.File]::ReadAllBytes($ServerExecutable))).Replace('-','').ToLower()
if($hash -ne $ExpectedSha256.ToLower()){throw 'Server SHA-256 mismatch.'}
if(@(Get-WmiObject Win32_Process|Where-Object {$_.Name -like 'rust-da-com-lab*.exe'}).Count){throw 'Close existing laboratory server processes first.'}
Add-Type -AssemblyName System.Web.Extensions
$json=New-Object System.Web.Script.Serialization.JavaScriptSerializer
$project=$json.DeserializeObject([IO.File]::ReadAllText($ProjectPath))
$port=[int]$project['diagnostic_port']
if($port -lt 1 -or $port -gt 65535){throw 'Invalid loopback port.'}
$url='http://127.0.0.1:'+$port+'/v1/snapshot'
$flag=Join-Path (Split-Path $ProjectPath) 'rust-da-source-offline.flag'
if(Test-Path $flag){throw 'Source marker exists; refusing to modify it.'}
$web=New-Object Net.WebClient;$web.Proxy=$null;$web.Encoding=[Text.Encoding]::UTF8
function Snapshot {return $json.DeserializeObject($web.DownloadString($url))}
$server=$null;$createdFlag=$false
try {
 & $ServerExecutable --self-test
 if($LASTEXITCODE -ne 0){throw 'Legacy in-process COM selftest failed.'}
 $server=Start-Process -FilePath $ServerExecutable -ArgumentList ('--volatile-server --project "'+$ProjectPath+'"') -WorkingDirectory (Split-Path $ServerExecutable) -WindowStyle Hidden -PassThru
 Start-Sleep -Seconds 2
 $check=Get-WmiObject Win32_Process -Filter ('ProcessId='+$server.Id)
 if(!$check -or $check.ExecutablePath -ne $ServerExecutable -or $check.CommandLine -notmatch ' --volatile-server(\s|$)'){throw 'Unexpected server identity.'}
 $online=Snapshot
 if([int]$online['pid'] -ne $server.Id){throw 'Cache belongs to a different process.'}
 foreach($s in $online['samples']){if([int]$s['quality'] -ne 192 -or !$s['source_timestamp_filetime']){throw 'Invalid online sample.'}}
 & $ServerExecutable --project-client-test --project $ProjectPath
 if($LASTEXITCODE -ne 0){throw 'Configured cross-process COM test failed.'}
 New-Item -ItemType File -Path $flag|Out-Null;$createdFlag=$true
 Start-Sleep -Seconds 1
 $bad=Snapshot;Start-Sleep -Seconds 1;$badAgain=Snapshot
 for($i=0;$i -lt $bad['samples'].Count;$i++){
  $a=$bad['samples'][$i];$b=$badAgain['samples'][$i]
  if([int]$a['quality'] -ne 8 -or [int]$b['quality'] -ne 8 -or $a['source_timestamp_filetime'] -ne $b['source_timestamp_filetime'] -or $a['value'] -ne $b['value']){throw 'Offline cache changed or remained Good.'}
 }
 Remove-Item -LiteralPath $flag;$createdFlag=$false;Start-Sleep -Seconds 1
 $recovered=Snapshot
 foreach($s in $recovered['samples']){if([int]$s['quality'] -ne 192){throw 'Recovery failed.'}}
 $request=[Net.HttpWebRequest]::Create($url);$request.Proxy=$null;$request.Method='POST';$request.ContentLength=0;$denied=$false
 try{$response=$request.GetResponse();$response.Close()}catch [Net.WebException] {if($_.Exception.Response -and [int]$_.Exception.Response.StatusCode -eq 405){$denied=$true; $_.Exception.Response.Close()}else{throw}}
 if(!$denied){throw 'Diagnostic endpoint accepted a mutating HTTP method.'}
 'PROJECT_RUNTIME_API_GOOD_BAD_STABLE_TIMESTAMP_GOOD_PASS server_pid='+$server.Id
 'PROJECT_DIAGNOSTIC_READ_ONLY_PASS status=405'
} finally {
 if($createdFlag -and (Test-Path $flag)){Remove-Item -LiteralPath $flag}
 if($server){$p=Get-WmiObject Win32_Process -Filter ('ProcessId='+$server.Id);if($p -and $p.ExecutablePath -eq $ServerExecutable -and $p.CommandLine -match ' --volatile-server(\s|$)'){Stop-Process -Id $p.ProcessId -Force;'STOP_TEST_SERVER='+$p.ProcessId}}
}
