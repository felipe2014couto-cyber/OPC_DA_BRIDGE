# SPDX-License-Identifier: MIT
# Exercises only a new, empty in-memory Scripting.Dictionary, never OPC.
param([string]$BridgeScript)
$ErrorActionPreference='Stop'
. $BridgeScript
$dictionary=New-Object -ComObject 'Scripting.Dictionary'
try{
 $signature=GetBridgeDispatchDescription $dictionary 'Exists'
 if($signature.parameter_count -lt 1 -or $signature.member -ne 'Exists'){throw 'Installed type information was not read'}
 'AUTOMATION_TYPEINFO_PASS Exists parameters='+$signature.parameter_count
 $r=InvokeBridgeDispatch $dictionary 'Count' ([object[]]@()) ([bool[]]@()) ([UInt16]2)
 if($r.hresult -ne 0 -or $r.value -ne 0){throw 'Empty dictionary Count failed'}
 $r=InvokeBridgeDispatch $dictionary 'Exists' ([object[]]@('lab-probe')) ([bool[]]@($false))
 if($r.value -isnot [bool] -or $r.value -ne $false){throw 'Boolean return failed'}
 $r=InvokeBridgeDispatch $dictionary 'Add' ([object[]]@('lab-probe',[double]7)) ([bool[]]@($false,$false))
 $r=InvokeBridgeDispatch $dictionary 'Exists' ([object[]]@('lab-probe')) ([bool[]]@($false))
 if($r.value -ne $true){throw 'String argument was not preserved'}
 $r=InvokeBridgeDispatch $dictionary 'Item' ([object[]]@('lab-probe')) ([bool[]]@($false)) ([UInt16]2)
 if($r.value -isnot [double] -or $r.value -ne 7){throw 'Double VARIANT was not preserved'}
 $failed=$false
 try{InvokeBridgeDispatch $dictionary 'BridgeMethodDoesNotExist' ([object[]]@()) ([bool[]]@())|Out-Null}catch{
  $failed=$true;$e=$_.Exception;while($e.InnerException){$e=$e.InnerException}
  if([Runtime.InteropServices.Marshal]::GetHRForException($e) -ne -2147352570){throw 'Unknown name HRESULT lost'}
 }
 if(!$failed){throw 'Unknown name was accepted'}
 'AUTOMATION_NATIVE_DISPATCH_PASS Count Exists string_argument Double unknown_name'
}finally{[Runtime.InteropServices.Marshal]::ReleaseComObject($dictionary)|Out-Null}
