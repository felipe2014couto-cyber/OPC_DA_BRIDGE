# SPDX-License-Identifier: MIT
# No COM activation, OPC access, registry, services, or network.
param([Parameter(Mandatory=$true)][string]$ConversionScript)
$ErrorActionPreference='Stop'
. $ConversionScript
$script:passed=0
function Check($value,[string]$type,$expected,[string]$clr){
 $result=ConvertBridgeScalar $value $type
 if($result -cne $expected -or $result.GetType().FullName -ne $clr){throw ('conversion_mismatch_'+$type)}
 $script:passed++
}
function Refuse($value,[string]$type){
 $failed=$false;try{$null=ConvertBridgeScalar $value $type}catch{$failed=$true}
 if(!$failed){throw ('unsafe_conversion_accepted_'+$type)}
 $script:passed++
}
Check ([single]12.5) 'Double' ([double]12.5) 'System.Double'
Check ([int]7) 'Double' ([double]7) 'System.Double'
Check '1.25e2' 'Double' ([double]125) 'System.Double'
Check $true 'Double' ([double]1) 'System.Double'
Check ([uint32]123) 'Integer' ([int]123) 'System.Int32'
Check ([double]-2147483648) 'Integer' ([int]-2147483648) 'System.Int32'
Check '2147483647' 'Integer' ([int]2147483647) 'System.Int32'
Check '+100' 'Integer' ([int]100) 'System.Int32'
Check $false 'Integer' ([int]0) 'System.Int32'
Check ([int]1) 'Boolean' $true 'System.Boolean'
Check ' FALSE ' 'Boolean' $false 'System.Boolean'
Check '1' 'Boolean' $true 'System.Boolean'
Check ([single]0) 'Boolean' $false 'System.Boolean'
Check ([double]1.25) 'String' '1.25' 'System.String'
Check $false 'String' 'false' 'System.String'
Check '' 'String' '' 'System.String'
$unicode='A'+[char]0+[char]0x00E7+[char]0xD83D+[char]0xDE00
Check $unicode 'String' $unicode 'System.String'
Refuse ([double]1.2) 'Integer'
Refuse ([double]1.0000000000000002) 'Integer'
Refuse '2147483648' 'Integer'
Refuse '1.2' 'Integer'
Refuse '1e2' 'Integer'
Refuse '1.000000000000000000000000000000000001' 'Integer'
Refuse ([double]::NaN) 'Double'
Refuse ([single]::PositiveInfinity) 'String'
Refuse ([long]9007199254740993) 'Double'
Refuse '1,25' 'Double'
Refuse '1,000' 'Integer'
Refuse ([int]2) 'Boolean'
Refuse 'ON' 'Boolean'
Refuse $null 'String'
Refuse ([int[]]@(1,2)) 'Double'
Refuse ('x'*4097) 'String'
Refuse ([string][char]0xD800) 'String'
$oldCulture=[Threading.Thread]::CurrentThread.CurrentCulture
try{
 [Threading.Thread]::CurrentThread.CurrentCulture=New-Object Globalization.CultureInfo('pt-BR')
 Check ([double]12.5) 'String' '12.5' 'System.String'
 Check '12.5' 'Double' ([double]12.5) 'System.Double'
}finally{[Threading.Thread]::CurrentThread.CurrentCulture=$oldCulture}
$types=@([bool]$true,[double]1,[single]1,[string]'x',[sbyte]1,[byte]1,[int16]1,[uint16]1,[int32]1,[uint32]1,[int64]1,[uint64]1)
$vts=@(11,5,4,8,16,17,2,18,3,19,20,21)
for($i=0;$i -lt $types.Count;$i++){
 if((BridgeObservedVariantType $types[$i]) -ne $vts[$i]){throw 'vartype_mismatch'}
 $script:passed++
}
'SCALAR_CONVERSION_PASS count='+$script:passed+' powershell='+$PSVersionTable.PSVersion+' architecture='+([IntPtr]::Size*8)
