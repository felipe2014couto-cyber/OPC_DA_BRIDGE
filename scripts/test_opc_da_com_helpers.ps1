# SPDX-License-Identifier: MIT
# Test the exact reflection helpers without COM, Registry or OPC connections.
param([string]$SourceScript)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Data
$functions=@(Get-Content -LiteralPath $SourceScript | Where-Object {$_ -match '^function Com(Get|Call)\('})
if($functions.Count -ne 2){throw 'Expected exactly the source ComGet and ComCall definitions'}
. ([ScriptBlock]::Create(($functions -join "`n")))
function OldComGet($object,[string]$name){return $object.GetType().InvokeMember($name,[Reflection.BindingFlags]::GetProperty,$null,$object,$null,[Globalization.CultureInfo]::InvariantCulture)}
$dataset=New-Object System.Data.DataSet
$before=OldComGet $dataset 'Tables'
if($null -ne $before){throw 'Failed to reproduce old empty collection return'}
$tables=ComGet $dataset 'Tables'
if($null -eq $tables -or $tables.GetType().FullName -ne 'System.Data.DataTableCollection' -or $tables.Count -ne 0){throw 'Empty collection lost its identity'}
$table=$dataset.Tables.Add('First')
$tables=ComGet $dataset 'Tables'
if($tables.GetType().FullName -ne 'System.Data.DataTableCollection' -or $tables.Count -ne 1){throw 'Nonempty collection was unrolled'}
$rows=ComCall $table 'Select' @()
if($null -eq $rows -or $rows.GetType().FullName -ne 'System.Data.DataRow[]' -or $rows.Count -ne 0){throw 'Method empty array was unrolled'}
if((ComGet $tables 'Count') -ne 1){throw 'Scalar property changed'}
$modifier=New-Object Reflection.ParameterModifier 4
$modifier[1]=$true;$modifier[2]=$true;$modifier[3]=$true
if(!$modifier[1] -or !$modifier[2] -or !$modifier[3]){throw 'Read output parameter mask is invalid'}
'POWERSHELL_VERSION='+$PSVersionTable.PSVersion.ToString()
'SOURCE_COM_HELPERS_PASS reproduced_old_empty_return empty_collection populated_collection method_empty_array scalar byref_mask'

. (Join-Path (Split-Path -Parent $SourceScript) 'opc_scalar_conversion.ps1')
$typeFunction=@(Get-Content -LiteralPath $SourceScript | Where-Object {$_ -match '^function ObservedVariantType\('})
if($typeFunction.Count -ne 1){throw 'Expected the observed VARIANT helper'}
. ([ScriptBlock]::Create($typeFunction[0]))
if((ObservedVariantType ([double]7)) -ne 5 -or (ObservedVariantType $false) -ne 11){throw 'Scalar VARIANT mapping changed'}
if((ObservedVariantType ([int]7)) -ne 3 -or (ObservedVariantType '7') -ne 8 -or $null -ne (ObservedVariantType $null)){throw 'Scalar type evidence was coerced'}
'SOURCE_VARIANT_TYPE_PASS double boolean unsupported null'

# Exercise the exact group helpers and CLR reference binder, without OPC.
$text=[IO.File]::ReadAllText($SourceScript)
foreach($name in @('ComInvokeReferences','SourceArrayFirst','ComGroupRead')){
 $definition=[regex]::Match($text,('(?ms)^function '+$name+'\([^\r\n]*\)\{.*?^\}'))
 if(!$definition.Success){throw ('Helper missing: '+$name)}
 . ([ScriptBlock]::Create($definition.Value))
}
$dictionary=New-Object 'System.Collections.Generic.Dictionary[System.String,System.Double]'
$dictionary.Add('probe',[double]7)
$result=ComInvokeReferences $dictionary 'TryGetValue' ([object[]]@('probe',[double]0)) ([bool[]]@($false,$true))
if($result[1] -isnot [double] -or $result[1] -ne 7){throw 'CLR byref output failed'}
function OneElement($value){$a=[Array]::CreateInstance([object],[int[]]@(1),[int[]]@(1));$a.SetValue($value,1);return ,$a}
$script:mockCalls=0;$script:addError=0;$script:readError=0
function ComInvokeReferences($target,[string]$name,[object[]]$values,[bool[]]$references){
 $script:mockCalls++
 if($name -eq 'AddItems'){
  if($values.Count -ne 5 -or $values[0] -ne 1 -or $values[1].GetLowerBound(0) -ne 1 -or $values[1].GetValue(1) -ne 'lab.source' -or $values[2].GetValue(1) -ne 42){throw 'AddItems inputs wrong'}
  if($references.Count -ne 5 -or $references[0] -or !$references[1] -or !$references[2] -or !$references[3] -or !$references[4]){throw 'AddItems output mask wrong'}
  if($values[3].GetType().GetElementType() -ne [int] -or $values[4].GetType().GetElementType() -ne [int]){throw 'AddItems output arrays not typed'}
  $values[3]=OneElement ([int]99);$values[4]=OneElement ([int]$script:addError)
 }elseif($name -eq 'SyncRead'){
  if($values.Count -ne 7 -or $values[0] -isnot [Int16] -or $values[0] -ne 2 -or $values[1] -ne 1 -or $values[2].GetValue(1) -ne 99){throw 'SyncRead inputs wrong'}
  if($references.Count -ne 7 -or $references[0] -or $references[1] -or !$references[2] -or !$references[3] -or !$references[4] -or !$references[5] -or !$references[6]){throw 'SyncRead output mask wrong'}
  if($values[3].GetType().GetElementType() -ne [object] -or $values[4].GetType().GetElementType() -ne [int]){throw 'SyncRead output arrays not typed'}
  $values[3]=OneElement ([double]7);$values[4]=OneElement ([int]$script:readError);$values[5]=OneElement ([int]192)
  $values[6]=OneElement ([DateTime]::SpecifyKind([DateTime]'2026-11-06T15:00:00',[DateTimeKind]::Utc))
 }else{throw 'Unexpected COM operation'}
 return ,$values
}
$r=ComGroupRead (New-Object PSObject) (New-Object PSObject) 'lab.source' 42
if($script:mockCalls -ne 2 -or $r.Count -ne 4 -or $r[1] -isnot [double] -or $r[1] -ne 7 -or $r[2] -ne 192 -or $r[3] -isnot [DateTime]){throw 'Group read outputs wrong'}
if((SourceArrayFirst (OneElement $false)) -ne $false){throw 'Boolean false lost'}
$script:addError=-1073479673;$script:mockCalls=0;$rejected=$false
try{ComGroupRead (New-Object PSObject) (New-Object PSObject) 'lab.source' 42|Out-Null}catch{$rejected=$true}
if(!$rejected -or $script:mockCalls -ne 1){throw 'Read attempted after AddItems failure'}
$script:addError=0;$script:readError=-2147024891;$rejected=$false
try{ComGroupRead (New-Object PSObject) (New-Object PSObject) 'lab.source' 42|Out-Null}catch{$rejected=$true}
if(!$rejected){throw 'Read error accepted'}
$rejected=$false;try{SourceArrayFirst ([object[]]@(1,2))|Out-Null}catch{$rejected=$true}
if(!$rejected){throw 'Invalid source array accepted'}
'SOURCE_GROUP_READ_MOCK_PASS CLR_byref one_based handles values quality timestamp add_error read_error invalid_array'

$definition=[regex]::Match($text,'(?ms)^function Timestamp\(\$date\)\{.*?^\}')
if(!$definition.Success){throw 'Timestamp helper missing'}
. ([ScriptBlock]::Create($definition.Value))
$naive=[DateTime]::SpecifyKind([DateTime]'2026-11-06T18:01:05.527',[DateTimeKind]::Unspecified)
$script:timestamp_policy='reject_unspecified'
if($null -ne (Timestamp $naive)){throw 'Unconfigured timezone was assumed'}
$script:timestamp_policy='utc'
$expected=[DateTime]::SpecifyKind($naive,[DateTimeKind]::Utc).ToFileTimeUtc()
if((Timestamp $naive) -ne $expected){throw 'UTC policy shifted timestamp'}
$script:timestamp_policy='local'
if((Timestamp $naive) -ne $naive.ToUniversalTime().ToFileTimeUtc()){throw 'Local policy changed'}
if($null -ne (Timestamp $null) -or $null -ne (Timestamp ([DateTime]'1500-01-01'))){throw 'Invalid timestamp replaced'}
$script:timestamp_policy='reject_unspecified'
if((Timestamp ([DateTime]::SpecifyKind($naive,[DateTimeKind]::Utc))) -ne $expected){throw 'Explicit UTC timestamp rejected'}
'SOURCE_TIMESTAMP_POLICY_PASS reject_unspecified utc local absent invalid no_clock_fallback'
