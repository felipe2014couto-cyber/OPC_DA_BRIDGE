# Run in the Win7 PowerShell x86 laboratory. Tests only a newly created temp folder.
param([Parameter(Mandatory=$true)][string]$AgentScript)
$ErrorActionPreference='Stop'
$source=[IO.File]::ReadAllText($AgentScript)
$match=[regex]::Match($source,'(?s)function Atomic\(\$p,\$text\)\s*\{.*?(?=\r?\nfunction Log\()')
if(!$match.Success){throw 'Could not find agent Atomic function'}
Invoke-Expression $match.Value
$utf8=New-Object Text.UTF8Encoding($false)
$dir=Join-Path ([IO.Path]::GetTempPath()) ('opc-agent-atomic-test-'+[Guid]::NewGuid().ToString('N'))
[IO.Directory]::CreateDirectory($dir)|Out-Null
try{
 $path=Join-Path $dir 'project.json'
 Atomic $path '{"value":28}'
 Atomic $path '{"value":7}'
 Atomic $path '{"value":7}'
 if([IO.File]::ReadAllText($path) -ne '{"value":7}'){throw 'Replacement content mismatch'}
 $lock=[IO.File]::Open($path,[IO.FileMode]::Open,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None)
 $failed=$false
 try{try{Atomic $path '{"value":99}'}catch{$failed=$true}}finally{$lock.Close()}
 if(!$failed -or [IO.File]::ReadAllText($path) -ne '{"value":7}'){throw 'Failed replacement did not retain the previous file'}
 Atomic $path '{"value":7}'
 if(@(Get-ChildItem $dir|Where-Object {$_.Name -like '*.tmp-*'}).Count){throw 'Temporary file remained'}
 'WINDOWS_ATOMIC_CREATE_REAPPLY_LOCK_FAILURE_RECOVERY_PASS'
}finally{[IO.Directory]::Delete($dir,$true)}
