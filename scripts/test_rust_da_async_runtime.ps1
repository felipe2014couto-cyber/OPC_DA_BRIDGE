# SPDX-License-Identifier: MIT
# Isolated read-only COM regression. Never registers COM or changes the live lab server.
param([Parameter(Mandatory=$true)][string]$LaboratoryDirectory,[Parameter(Mandatory=$true)][string]$ExpectedSha256)
$ErrorActionPreference='Stop'
if([IntPtr]::Size -ne 4){throw 'Use x86 PowerShell'}
$original=Join-Path $LaboratoryDirectory 'rust-da-com-lab.exe'
$hash=[BitConverter]::ToString(([Security.Cryptography.SHA256]::Create()).ComputeHash([IO.File]::ReadAllBytes($original))).Replace('-','').ToLowerInvariant()
if($hash -ne $ExpectedSha256.ToLowerInvariant()){throw 'Executable hash mismatch'}
$root=Join-Path $LaboratoryDirectory ('async-fixture-'+[Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $root | Out-Null
$exe=Join-Path $root 'rust-da-com-lab.exe';Copy-Item -LiteralPath $original -Destination $exe
$stop=Join-Path $root 'async-test-stop.flag'
$server=$null;$client=$null
try {
 $server=Start-Process -FilePath $exe -ArgumentList '--volatile-server --isolated-source-test --async-test-server' -WorkingDirectory $root -PassThru -RedirectStandardOutput (Join-Path $root 'server.out') -RedirectStandardError (Join-Path $root 'server.err')
 $serverHandle=$server.Handle
 Start-Sleep -Seconds 1
 $check=Get-WmiObject Win32_Process -Filter ('ProcessId='+$server.Id)
 if(!$check -or $check.ExecutablePath -ne $exe -or $check.CommandLine -notmatch '--isolated-source-test'){throw 'Unexpected isolated process'}
 'TEST_SERVER_PID='+$server.Id+' PATH='+$exe+' SHA256='+$hash
 $out=Join-Path $root 'client.out';$err=Join-Path $root 'client.err'
 $client=Start-Process -FilePath $exe -ArgumentList '--async-client-test --isolated-source-test' -WorkingDirectory $root -PassThru -RedirectStandardOutput $out -RedirectStandardError $err
 $clientHandle=$client.Handle
 if(!$client.WaitForExit(25000)){
  $check=Get-WmiObject Win32_Process -Filter ('ProcessId='+$client.Id)
  if($check.ExecutablePath -eq $exe -and $check.CommandLine -match '--async-client-test'){$client.Kill();$client.WaitForExit()}
  throw 'Own test client timed out'
 }
 $client.WaitForExit();$code=$client.ExitCode
 'TEST_CLIENT_PID='+$client.Id+' EXIT='+$code
 Get-Content $out
 if((Get-Item $err).Length){Get-Content $err}
 if($null -eq $code -or $code -ne 0 -or !(Select-String -Path $out -Pattern '^ASYNC_COM_INTERPROCESS_PASS ' -Quiet)){throw 'Cross-process callback test failed'}
 'ASYNC_RUNTIME_TEST_PASS evidence='+$root
}finally{
 if($server -and !$server.HasExited){
  $check=Get-WmiObject Win32_Process -Filter ('ProcessId='+$server.Id)
  if(!$check -or $check.ExecutablePath -ne $exe -or $check.CommandLine -notmatch '--async-test-server'){throw 'Refusing stop: process identity changed'}
  [IO.File]::WriteAllText($stop,'stop')
  if(!$server.WaitForExit(8000)){throw 'Own isolated test server did not stop normally'}
  $server.WaitForExit()
 }
 if($server){'TEST_SERVER_EXIT='+$server.ExitCode}
}
