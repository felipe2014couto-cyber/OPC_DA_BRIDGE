# SPDX-License-Identifier: MIT
# Test a separate volatile OPC DA source fixture. No Registry writes or OPC writes.
param([string]$LaboratoryDirectory,[string]$ExpectedSha256)
$ErrorActionPreference='Stop'
$utf8=New-Object Text.UTF8Encoding($false)
$exe=Join-Path $LaboratoryDirectory 'rust-da-com-lab.exe'
$log=Join-Path $LaboratoryDirectory 'rust-da-com-lab.log'
$override=Join-Path $LaboratoryDirectory 'rust-da-source-values.json'
$offline=Join-Path $LaboratoryDirectory 'rust-da-source-offline.flag'
$stop=Join-Path $LaboratoryDirectory 'rust-da-source-stop.flag'
$project=Join-Path $LaboratoryDirectory 'project.json'
if(!(Test-Path $exe)){throw 'Fixture executable is missing'}
if(Test-Path $project){throw 'Refusing to run source fixture in a destination project folder'}
foreach($p in @($override,$offline,$stop)){if(Test-Path $p){throw ('Refusing to overwrite existing fixture state: '+[IO.Path]::GetFileName($p))}}
$sha=[Security.Cryptography.SHA256]::Create()
$hash=[BitConverter]::ToString($sha.ComputeHash([IO.File]::ReadAllBytes($exe))).Replace('-','').ToLowerInvariant()
if($hash -ne $ExpectedSha256.ToLowerInvariant()){throw 'Fixture executable SHA-256 mismatch'}
$existing=@(Get-WmiObject Win32_Process -Filter "Name='rust-da-com-lab.exe'" | Where-Object {$_.ExecutablePath -eq $exe})
if($existing.Count){throw 'Fixture executable is already active; refusing to attach or stop it'}
$nonce=[Guid]::NewGuid().ToString('N')
$serverOut=Join-Path $LaboratoryDirectory ('source-server-'+$nonce+'.out')
$serverErr=Join-Path $LaboratoryDirectory ('source-server-'+$nonce+'.err')
$server=$null
function Invoke-SourceClient([string]$label,[bool]$expectSuccess,[string]$expectedNumeric,[string]$expectedBoolean){
 $out=Join-Path $LaboratoryDirectory ('source-client-'+$label+'-'+$nonce+'.out')
 $err=Join-Path $LaboratoryDirectory ('source-client-'+$label+'-'+$nonce+'.err')
 $client=Start-Process -FilePath $exe -ArgumentList '--source-lab-client-test' -WorkingDirectory $LaboratoryDirectory -PassThru -RedirectStandardOutput $out -RedirectStandardError $err
 if(!$client.WaitForExit(15000)){throw ('Source COM client timeout: '+$client.Id)}
 $remaining=Get-WmiObject Win32_Process -Filter ('ProcessId='+$client.Id)
 if($remaining){throw ('Source COM client did not exit: '+$client.Id)}
 $text=''
 if(Test-Path $out){$text=[IO.File]::ReadAllText($out,$utf8)}
 if(Test-Path $err){$text+=[IO.File]::ReadAllText($err,$utf8)}
 Write-Output ('--- '+$label+' ---')
 Write-Output 'CLIENT_TERMINATED=true'
 Write-Output $text.Trim()
 if($expectSuccess){
  if($text -notmatch 'SOURCE_LAB_COM_CROSS_PROCESS_PASS' -or $text -match 'source-lab cross-process COM test failed'){throw ('Cross-process COM read failed in '+$label)}
  if($expectedNumeric -and $text -notmatch [regex]::Escape($expectedNumeric)){throw ('Unexpected numeric sample in '+$label)}
  if($expectedBoolean -and $text -notmatch [regex]::Escape($expectedBoolean)){throw ('Unexpected boolean sample in '+$label)}
 }else{
  if($text -match 'SOURCE_LAB_COM_CROSS_PROCESS_PASS' -or $text -notmatch '0x0008|0xC0040008|0xc0040008'){throw 'Source loss did not return a failed/Bad COM read'}
 }
}
try{
 $server=Start-Process -FilePath $exe -ArgumentList '--source-lab-server -Embedding' -WorkingDirectory $LaboratoryDirectory -PassThru -RedirectStandardOutput $serverOut -RedirectStandardError $serverErr
 $ready=$false
 for($i=0;$i -lt 40;$i++){
  Start-Sleep -Milliseconds 250
  $proc=Get-WmiObject Win32_Process -Filter ('ProcessId='+$server.Id)
  if(!$proc -or $proc.ExecutablePath -ne $exe -or $proc.CommandLine -notmatch '--source-lab-server'){throw 'Source fixture process identity mismatch'}
  if((Test-Path $log) -and [IO.File]::ReadAllText($log,$utf8).Contains('class factory ready')){$ready=$true;break}
 }
 if(!$ready){throw 'Source fixture did not report COM class registration'}
 Invoke-SourceClient 'initial' $true '' ''
 [IO.File]::WriteAllText($override,'{"numeric":41.25,"boolean":false}',$utf8)
 Start-Sleep -Milliseconds 500
 Invoke-SourceClient 'changed-values' $true 'F64(41.25)' 'Bool(false)'
 [IO.File]::WriteAllText($offline,'source unavailable',$utf8)
 Start-Sleep -Milliseconds 500
 Invoke-SourceClient 'source-unavailable' $false '' ''
 Remove-Item -LiteralPath $offline
 Start-Sleep -Milliseconds 500
 Invoke-SourceClient 'recovery' $true 'F64(41.25)' 'Bool(false)'
 $proc=Get-WmiObject Win32_Process -Filter ('ProcessId='+$server.Id)
 if(!$proc -or $proc.ExecutablePath -ne $exe){throw 'Source PID/path changed during the test'}
 Write-Output ('SOURCE_FIXTURE_PASS pid='+$server.Id+' clsid={C7482207-6725-496D-B846-A0B31B941937} registration=process-only registry=unchanged')
}finally{
 if($server){
  $proc=Get-WmiObject Win32_Process -Filter ('ProcessId='+$server.Id)
  if($proc -and $proc.ExecutablePath -eq $exe -and $proc.CommandLine -match '--source-lab-server'){
   [IO.File]::WriteAllText($stop,'stop',$utf8)
   if(!$server.WaitForExit(10000)){Write-Warning ('Fixture did not stop normally; no forced termination attempted. PID='+$server.Id)}
  }
 }
 if(Test-Path $offline){Remove-Item -LiteralPath $offline}
 if(Test-Path $override){Remove-Item -LiteralPath $override}
}
