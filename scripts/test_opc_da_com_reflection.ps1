# SPDX-License-Identifier: MIT
# Regression for PSObject wrappers in reflection COM calls. No OPC connection.
param([string]$SourceScript)
$ErrorActionPreference='Stop'
$defs=@(Get-Content -LiteralPath $SourceScript | Where-Object {$_ -match '^function Com(Get|Call)\('})
if($defs.Count -ne 2){throw 'Reflection helpers missing'}
. ([ScriptBlock]::Create(($defs -join "`n")))
function OldComCall($target,[string]$name,[object[]]$values){return ,($target.GetType().InvokeMember($name,[Reflection.BindingFlags]::InvokeMethod,$null,$target,$values,[Globalization.CultureInfo]::InvariantCulture))}
$directory=Split-Path -Parent $SourceScript
$fixture=Join-Path $directory ('reflection-fixture-'+[Guid]::NewGuid().ToString('N')+'.txt')
[IO.File]::WriteAllText($fixture,'probe-value')
$fso=$null;$stream=$null
try{
 $fso=New-Object -ComObject 'Scripting.FileSystemObject'
 $oldFailed=$false
 try{$stream=OldComCall $fso 'OpenTextFile' @($fixture,[int]1,$false)}catch{$oldFailed=$true}
 if(!$oldFailed){throw 'Original PS2 failure not reproduced'}
 $stream=ComCall $fso 'OpenTextFile' @($fixture,[int]1,$false)
 if($stream.Read(1) -ne 'p'){throw 'COM return object lost its methods'}
 if((ComGet $stream 'AtEndOfStream') -ne $false){throw 'COM property wrong'}
 'COM_REFLECTION_UNWRAP_PASS reproduced_old_failure read_own_fixture property'
}finally{
 if($stream){try{$stream.Close()}finally{[Runtime.InteropServices.Marshal]::ReleaseComObject($stream)|Out-Null}}
 if($fso){[Runtime.InteropServices.Marshal]::ReleaseComObject($fso)|Out-Null}
 if(Test-Path -LiteralPath $fixture){Remove-Item -LiteralPath $fixture}
}
