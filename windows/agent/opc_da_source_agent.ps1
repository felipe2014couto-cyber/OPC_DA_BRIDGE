# SPDX-License-Identifier: MIT
# Local read-only OPC DA agent. No remote registry, registration, service or writes.
# Windows PowerShell 2/.NET 2 compatible; process architecture must match Automation.
param([string]$RequestBase64='', [switch]$Worker, [string]$ResultFile='', [switch]$LibraryOnly)
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false)
Add-Type -AssemblyName System.Web.Extensions
$json=New-Object System.Web.Script.Serialization.JavaScriptSerializer
$json.MaxJsonLength=1048576
$root=Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $root 'opc_scalar_conversion.ps1')
$utf8=New-Object Text.UTF8Encoding($false)
function Encode($v){
 if($null -eq $v){return 'null'}
 if($v -is [string]){return $json.Serialize([string]$v)}
 if($v -is [bool]){if($v){return 'true'}else{return 'false'}}
 if($v -is [Collections.IDictionary]){$a=@();foreach($k in $v.Keys){$a+=($json.Serialize([string]$k)+':'+(Encode $v[$k]))};return '{'+($a -join ',')+'}'}
 if($v -is [Collections.IEnumerable]){$a=@();foreach($i in $v){$a+=(Encode $i)};return '['+($a -join ',')+']'}
 if($v -is [ValueType]){return [Convert]::ToString($v,[Globalization.CultureInfo]::InvariantCulture)}
 throw 'unsupported_json'
}
function Output($v){$text=Encode $v;if($Worker){[IO.File]::WriteAllText($ResultFile,$text,$utf8)}else{[Console]::Out.WriteLine($text)}}
function Log($s){[IO.File]::AppendAllText((Join-Path $root 'source-agent.log'),([DateTime]::UtcNow.ToString('o')+' '+$s+"`r`n"),$utf8)}
function Identity {
 $ips=@(Get-WmiObject Win32_NetworkAdapterConfiguration|Where-Object {$_.IPEnabled}|ForEach-Object {$_.IPAddress})
 return @{hostname=([string](Get-WmiObject Win32_ComputerSystem).Name);addresses=$ips;registry_view=([IntPtr]::Size*8).ToString()+'-bit';process_architecture=([IntPtr]::Size*8);os_version=[Environment]::OSVersion.VersionString}
}
function Timestamp($date){
 if(!($date -is [DateTime]) -or $date.Year -lt 1601){return $null}
 try{
  if($date.Kind -eq [DateTimeKind]::Unspecified){
   if($script:timestamp_policy -eq 'utc'){$date=[DateTime]::SpecifyKind($date,[DateTimeKind]::Utc)}
   elseif($script:timestamp_policy -eq 'local'){
    if([TimeZoneInfo]::Local.IsInvalidTime($date) -or [TimeZoneInfo]::Local.IsAmbiguousTime($date)){return $null}
    $date=[DateTime]::SpecifyKind($date,[DateTimeKind]::Local)
   }else{return $null}
  }
  $t=$date.ToUniversalTime().ToFileTimeUtc();if($t -gt 0){return $t}
 }catch{}
 return $null
}
function ErrorCode($exception){while($exception.InnerException){$exception=$exception.InnerException};return [Runtime.InteropServices.Marshal]::GetHRForException($exception)}
function Discover {
 $servers=@();$categories=@('{63D5F430-CFE4-11D1-B2C8-0060083BA1FB}','{63D5F432-CFE4-11D1-B2C8-0060083BA1FB}','{CC603642-66D7-48F1-B69A-B625E73652D7}')
 $base=[Microsoft.Win32.Registry]::ClassesRoot.OpenSubKey('CLSID')
 try{
  foreach($clsid in $base.GetSubKeyNames()){
   $found=@();foreach($category in $categories){$k=$base.OpenSubKey($clsid+'\Implemented Categories\'+$category);if($k){$found+=$category;$k.Close()}}
   if(!$found.Count){continue}
   $key=$base.OpenSubKey($clsid+'\ProgID');if(!$key){continue}
   try{$prog=[string]$key.GetValue('')}finally{$key.Close()}
   if($prog -notmatch '^[A-Za-z0-9_.-]{1,200}$'){continue}
   $servers+=@{prog_id=$prog;clsid=$clsid;categories=$found;registry_view=$identity['registry_view'];availability_tested=$false}
  }
 }finally{if($base){$base.Close()}}
 # Legacy DA 1.0 registrations can use ProgID\OPC without a category.
 foreach($name in [Microsoft.Win32.Registry]::ClassesRoot.GetSubKeyNames()){
  if($name -notmatch '^[A-Za-z0-9_. -]{1,200}$'){continue}
  $marker=[Microsoft.Win32.Registry]::ClassesRoot.OpenSubKey($name+'\OPC')
  if(!$marker){continue};$marker.Close()
  $key=[Microsoft.Win32.Registry]::ClassesRoot.OpenSubKey($name+'\CLSID');if(!$key){continue}
  try{$id=[string]$key.GetValue('')}finally{$key.Close()}
  if($id -notmatch '^\{[0-9a-fA-F-]{36}\}$'){continue}
  if(!@($servers|Where-Object {$_['prog_id'] -eq $name -and $_['clsid'] -eq $id}).Count){$servers+=@{prog_id=$name;clsid=$id;categories=@();registry_view=$identity['registry_view'];availability_tested=$false;registration='legacy_OPC_marker'}}
 }
 return @($servers|Sort-Object {$_.prog_id})
}
# PowerShell enumerates function return values. Preserve the RCW/collection itself,
# including empty OPCGroups/OPCItems; otherwise an empty collection becomes $null.
function ComGet($object,[string]$name){$script:com_phase='get_'+$name;$base=$object.PSObject.BaseObject;return ,($base.GetType().InvokeMember($name,[Reflection.BindingFlags]::GetProperty,$null,$base,$null,[Globalization.CultureInfo]::InvariantCulture))}
function ComCall($object,[string]$name,[object[]]$arguments){$script:com_phase='call_'+$name;$base=$object.PSObject.BaseObject;$clean=New-Object 'Object[]' $arguments.Count;for($i=0;$i -lt $arguments.Count;$i++){if($null -ne $arguments[$i]){$clean[$i]=$arguments[$i].PSObject.BaseObject}};return ,($base.GetType().InvokeMember($name,[Reflection.BindingFlags]::InvokeMethod,$null,$base,$clean,[Globalization.CultureInfo]::InvariantCulture))}
function ObservedVariantType($value){return (BridgeObservedVariantType $value)}
function ComInvokeReferences($object,[string]$name,[object[]]$values,[bool[]]$references){
 if($values.Count -ne $references.Count){throw 'reference_mask_mismatch'}
 $base=$object.PSObject.BaseObject
 for($i=0;$i -lt $values.Count;$i++){if($null -ne $values[$i]){$values[$i]=$values[$i].PSObject.BaseObject}}
 $modifier=New-Object Reflection.ParameterModifier $values.Count
 for($i=0;$i -lt $values.Count;$i++){$modifier[$i]=$references[$i]}
 $modifiers=[Reflection.ParameterModifier[]]@($modifier)
 $base.GetType().InvokeMember($name,[Reflection.BindingFlags]::InvokeMethod,$null,$base,$values,$modifiers,[Globalization.CultureInfo]::InvariantCulture,$null)|Out-Null
 return ,$values
}
function SourceArrayFirst($array){
 if($array -isnot [Array] -or $array.Rank -ne 1 -or $array.Length -ne 1){throw 'invalid_source_array'}
 return ,($array.GetValue($array.GetLowerBound(0)))
}
function ComGroupRead($group,$items,[string]$itemId,[int]$clientHandle){
 if($script:keep_source_session -and $script:source_session -and $script:source_session['handles'].ContainsKey($itemId)){
  $handles=[Array]::CreateInstance([int],[int[]]@(1),[int[]]@(1));$handles.SetValue([int]$script:source_session['handles'][$itemId],1)
 }else{
 $ids=[Array]::CreateInstance([string],[int[]]@(1),[int[]]@(1));$ids.SetValue($itemId,1)
 $clients=[Array]::CreateInstance([int],[int[]]@(1),[int[]]@(1));$clients.SetValue($clientHandle,1)
 $argsAdd=New-Object 'Object[]' 5
 $argsAdd[0]=[int]1;$argsAdd[1]=$ids;$argsAdd[2]=$clients
 $argsAdd[3]=[Array]::CreateInstance([int],[int[]]@(1),[int[]]@(1))
 $argsAdd[4]=[Array]::CreateInstance([int],[int[]]@(1),[int[]]@(1))
 $script:com_phase='group_AddItems'
 $step=[Diagnostics.Stopwatch]::StartNew()
 $added=ComInvokeReferences $items 'AddItems' $argsAdd ([bool[]]@($false,$true,$true,$true,$true))
 $script:source_add_ms+=$step.Elapsed.TotalMilliseconds
 $hr=[int](SourceArrayFirst $added[4])
 if($hr -lt 0){throw (New-Object Runtime.InteropServices.COMException -ArgumentList @('AddItems item failed',$hr))}
 $handles=$added[3]
 if($script:keep_source_session -and $script:source_session){$script:source_session['handles'][$itemId]=[int](SourceArrayFirst $handles)}
 }
 $script:last_source_handle=[int](SourceArrayFirst $handles)
 $argsRead=New-Object 'Object[]' 7
 $argsRead[0]=[Int16]2;$argsRead[1]=[int]1;$argsRead[2]=$handles
 $argsRead[3]=[Array]::CreateInstance([object],[int[]]@(1),[int[]]@(1))
 $argsRead[4]=[Array]::CreateInstance([int],[int[]]@(1),[int[]]@(1))
 $script:com_phase='group_SyncRead'
 $step=[Diagnostics.Stopwatch]::StartNew()
 $read=ComInvokeReferences $group 'SyncRead' $argsRead ([bool[]]@($false,$false,$true,$true,$true,$true,$true))
 $script:source_read_ms+=$step.Elapsed.TotalMilliseconds
 $hr=[int](SourceArrayFirst $read[4])
 if($hr -lt 0){throw (New-Object Runtime.InteropServices.COMException -ArgumentList @('SyncRead item failed',$hr))}
 $value=SourceArrayFirst $read[3]
 $quality=0;if($null -ne $read[5]){$quality=[int](SourceArrayFirst $read[5])}
 $stamp=$null;if($null -ne $read[6]){$stamp=SourceArrayFirst $read[6]}
 return ,([object[]]@([Int16]2,$value,$quality,$stamp))
}
function ReadItems($request){
 $server=$null;$group=$null;$connected=$false;$samples=@()
 $script:source_add_ms=[double]0;$script:source_read_ms=[double]0
 $script:source_init_ms=[double]0
 $initializing=[Diagnostics.Stopwatch]::StartNew()
 try{
  if($script:keep_source_session -and $script:source_session){
   $server=$script:source_session['server'];$group=$script:source_session['group'];$groups=$script:source_session['groups'];$items=$script:source_session['items'];$connected=$true
  }else{
  Log 'COM_AUTOMATION_CREATE'
  $server=New-Object -ComObject 'OPC.Automation.1'
  Log 'COM_AUTOMATION_CREATED'
  Log 'COM_CONNECT_BEGIN'
  (ComCall $server 'Connect' @([string]$request['prog_id']))|Out-Null;$connected=$true
  Log 'COM_CONNECTED'
  $groups=ComGet $server 'OPCGroups'
  Log ('COM_GROUP_COLLECTION null='+($null -eq $groups))
  $group=ComCall $groups 'Add' @('OpcBridgeReadOnly_'+[Guid]::NewGuid().ToString('N'))
  Log 'COM_GROUP_CREATED'
  $items=ComGet $group 'OPCItems'
  if($script:keep_source_session){$script:source_session=@{server=$server;group=$group;groups=$groups;items=$items;handles=@{}}}
  $script:source_init_ms=$initializing.Elapsed.TotalMilliseconds
  }
  $index=0
  foreach($entry in $request['items']){
   $index++;$item=$null
   $targetType=[string]$entry['data_type'];$sourceType=[string]$entry['source_data_type'];if(!$sourceType){$sourceType=$targetType};$conversion=$entry['source_conversion'];if([string]::IsNullOrEmpty([string]$conversion)){$conversion=$null}
   $s=@{item_id=$entry['item_id'];source_item_id=$entry['source_item_id'];data_type=$targetType;source_data_type=$sourceType;source_conversion=$conversion;value=$null;quality=0;source_timestamp_filetime=$null;hresult=0;source_vartype=$null;published_vartype=(BridgePublishedVariantType $targetType);timestamp_interpretation=('Unspecified OLE date policy: '+$script:timestamp_policy);timestamp_policy=$script:timestamp_policy}
   try{
    Log ('COM_GROUP_ITEM_READ index='+$index)
    $out=ComGroupRead $group $items ([string]$entry['source_item_id']) $index;$value=$out[1]
    $s['read_method']='OPCGroup.SyncRead';$s['source_server_handle']=$script:last_source_handle
    # Validate the returned VARIANT, never a tag name or requested type. Some
    # installed Automation implementations omit CanonicalDataType. Its absence
    # must not discard a successfully read, strictly typed scalar sample.
    $observedVartype=ObservedVariantType $value;$s['source_vartype']=$observedVartype
    $s['type_evidence']='returned_VARIANT_CLR_type'
    $s['canonical_vartype']=$null

    if($out[3] -is [DateTime]){$s['source_timestamp_text']=$out[3].ToString('o');$s['source_timestamp_kind']=$out[3].Kind.ToString()}
    $s['source_timestamp_filetime']=Timestamp $out[3]
    $s['source_quality']=([int]$out[2] -band 65535)
    $sourceMatches=($sourceType -eq 'Boolean' -and $value -is [bool] -and $observedVartype -eq 11) -or ($sourceType -eq 'Double' -and $value -is [double] -and $observedVartype -eq 5 -and ![double]::IsNaN($value) -and ![double]::IsInfinity($value)) -or ($sourceType -eq 'Float' -and $value -is [single] -and $observedVartype -eq 4 -and ![single]::IsNaN($value) -and ![single]::IsInfinity($value))
    $conversionAllowed=(!$conversion -and $sourceType -eq $targetType) -or ($conversion -eq 'float_to_double' -and $sourceType -eq 'Float' -and $targetType -eq 'Double')
    if($conversion -eq 'automatic' -and $sourceType -eq 'Auto'){
     try{$publishedValue=ConvertBridgeScalar $value $targetType;$sourceMatches=$true;$conversionAllowed=$true}
     catch{$sourceMatches=$false;$s['conversion_error']=$_.Exception.Message}
    }elseif(!$conversion -and $sourceType -eq $targetType -and ($targetType -eq 'Integer' -or $targetType -eq 'String')){
     $sourceMatches=($observedVartype -eq (BridgePublishedVariantType $targetType))
     if($sourceMatches){try{$publishedValue=ConvertBridgeScalar $value $targetType}catch{$sourceMatches=$false}}
    }
    if($s['canonical_vartype'] -ne $null -and $s['canonical_vartype'] -ne $observedVartype){$sourceMatches=$false}
    if(!$sourceMatches -or !$conversionAllowed){$s['hresult']=-1073479676;$s['quality']=0}else{
     if($conversion -ne 'automatic'){$publishedValue=$value}
     if($conversion -eq 'float_to_double'){$publishedValue=[double]$value;$s['type_evidence']='COM_VARIANT_VT_R4_explicitly_converted_to_VT_R8'}
     $s['value']=$publishedValue;$s['quality']=([int]$out[2] -band 65535)
     if($conversion -eq 'automatic'){$s['type_evidence']='returned_VARIANT_CLR_type_automatically_converted'}
     if(!$s['source_timestamp_filetime'] -and ($s['quality'] -band 192) -eq 192){$s['quality']=64}
    }
   }catch{$s['hresult']=ErrorCode $_.Exception;$s['quality']=0;$s['diagnostic_stage']=$script:com_phase;$s['diagnostic_error_id']=($_.FullyQualifiedErrorId -split ',')[0];$s['exception_type']=$_.Exception.GetType().FullName;Log ('COM_ITEM_ERROR index='+$index+' hresult='+$s['hresult']+' phase='+$script:com_phase)
    # Optional diagnostics inspect the installed type information, not OPC IDLs.
    if($s['hresult'] -eq -2147352571 -and (Test-Path (Join-Path $root 'automation_dispatch.ps1'))){
     try{
      if(!$script:sourceSignatures){
       . (Join-Path $root 'automation_dispatch.ps1')
       $script:sourceSignatures=@{add_items=(GetBridgeDispatchDescription $items 'AddItems');sync_read=(GetBridgeDispatchDescription $group 'SyncRead')}
      }
      $s['automation_signatures']=$script:sourceSignatures
     }catch{$s['signature_hresult']=ErrorCode $_.Exception}
    }
   }
   finally{if($item){[Runtime.InteropServices.Marshal]::ReleaseComObject($item)|Out-Null}}
   $samples+=$s
  }
 }catch{$script:failure_phase=$script:com_phase;throw}
 finally{
  if(!$script:keep_source_session -or !$script:source_session){
   if($connected){try{(ComCall $server 'Disconnect' @())|Out-Null}catch{}}
   if($group){[Runtime.InteropServices.Marshal]::ReleaseComObject($group)|Out-Null}
   if($server){[Runtime.InteropServices.Marshal]::ReleaseComObject($server)|Out-Null}
  }
 }
 return $samples
}
function ClosePersistentSource {
 if($script:source_session){
  try{(ComCall $script:source_session['server'] 'Disconnect' @())|Out-Null}catch{}
  foreach($name in @('items','group','groups','server')){try{[Runtime.InteropServices.Marshal]::ReleaseComObject($script:source_session[$name])|Out-Null}catch{}}
  $script:source_session=$null
 }
}
function StopRustDaSourceLabServers([string]$executable){
 $full=[IO.Path]::GetFullPath($executable)
 $marker=Join-Path (Split-Path -Parent $full) 'rust-da-source-stop.flag'
 $created=$false;$stream=$null
 try{
  $stream=[IO.File]::Open($marker,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
  $created=$true;$bytes=$utf8.GetBytes([DateTime]::UtcNow.ToString('o'))
  $stream.Write($bytes,0,$bytes.Length);$stream.Close();$stream=$null
 }catch{
  if($stream){try{$stream.Close()}catch{}}
  if(Test-Path -LiteralPath $marker){throw 'source_fixture_cleanup_marker_present'}
  throw 'source_fixture_cleanup_failed'
 }
 Log 'SOURCE_FIXTURE_STOP_REQUESTED'
 try{
  $quiet=0
  for($attempt=0;$attempt -lt 180;$attempt++){
   $servers=@(Get-WmiObject Win32_Process -Filter "Name='rust-da-com-lab.exe'" | Where-Object {
    $_.ExecutablePath -and [IO.Path]::GetFullPath($_.ExecutablePath) -ieq $full -and
    $_.CommandLine -match '--source-lab-server\s+-Embedding'
   })
   if($servers.Count -eq 0){$quiet++;if($quiet -ge 2){Log 'SOURCE_FIXTURE_STOP_CONFIRMED';return}}
   else{$quiet=0}
   Start-Sleep -Milliseconds 250
  }
  Log 'SOURCE_FIXTURE_STOP_TIMEOUT'
  throw 'source_fixture_cleanup_failed'
 }finally{
  if($created){try{[IO.File]::Delete($marker)}catch{Log 'SOURCE_FIXTURE_STOP_MARKER_REMOVE_FAILED';throw 'source_fixture_cleanup_failed'}}
 }
}
function ReadRustDaFixture($request,$binding){
 $fixture=$binding['rust_da_com_fixture']
 if(!$fixture -or $fixture['enabled'] -ne $true -or [string]$request['prog_id'] -ne [string]$fixture['prog_id'] -or [string]$request['clsid'] -ne [string]$fixture['clsid']){throw 'source_fixture_not_authorized'}
 $fixtureItems=@($fixture['items'])
 if($fixtureItems.Count -ne 2 -or [string]$fixtureItems[0]['data_type'] -ne 'Double' -or [string]$fixtureItems[1]['data_type'] -ne 'Boolean' -or [string]$fixtureItems[0]['source_item_id'] -eq [string]$fixtureItems[1]['source_item_id']){throw 'source_fixture_catalog_mismatch'}
 $exe=[string]$fixture['executable'];if(!(Test-Path -LiteralPath $exe)){throw 'source_fixture_unavailable'}
 $hash=[BitConverter]::ToString(([Security.Cryptography.SHA256]::Create()).ComputeHash([IO.File]::ReadAllBytes($exe))).Replace('-','').ToLowerInvariant()
 if($hash -ne ([string]$fixture['sha256']).ToLowerInvariant()){throw 'source_fixture_hash_mismatch'}
 if(@($request['items']).Count -ne $fixtureItems.Count){throw 'invalid_item_count'}
 $expected=@{}
 foreach($configured in $fixtureItems){$expected[[string]$configured['source_item_id']]=[string]$configured['data_type']}
 foreach($entry in $request['items']){if(!$expected.ContainsKey([string]$entry['source_item_id']) -or $expected[[string]$entry['source_item_id']] -ne [string]$entry['data_type']){throw 'invalid_item_id'}}
 $psi=New-Object Diagnostics.ProcessStartInfo;$psi.FileName=$exe;$psi.Arguments='--source-lab-client-test';$psi.WorkingDirectory=(Split-Path -Parent $exe);$psi.UseShellExecute=$false;$psi.CreateNoWindow=$true;$psi.RedirectStandardOutput=$true;$psi.RedirectStandardError=$true
 $process=[Diagnostics.Process]::Start($psi)
 try{
  if(!$process.WaitForExit(12000)){$process.Kill();$process.WaitForExit();throw 'com_timeout'}
  $output=$process.StandardOutput.ReadToEnd();$stderr=$process.StandardError.ReadToEnd()
 }finally{
  $process.Close()
  StopRustDaSourceLabServers $exe
 }
 $samples=@()
 foreach($line in ($output -split "`r?`n")){
  $match=[regex]::Match($line,'^SOURCE_SAMPLE index=(?<index>[01]) variant=(?<variant>F64\([^)]*\)|Bool\((true|false)\)) quality=(?<quality>0x[0-9a-fA-F]+) timestamp_filetime=(?<ticks>\d+) read_hresult=S_OK method=IOPCSyncIO::Read$')
  if(!$match.Success){continue}
  $index=[int]$match.Groups['index'].Value;$ticks=[long]$match.Groups['ticks'].Value;$quality=[Convert]::ToInt32($match.Groups['quality'].Value.Substring(2),16)
  if($index -eq 0){$sourceId=[string]$fixtureItems[0]['source_item_id'];$vartype=5;$valueText=$match.Groups['variant'].Value.Substring(4).TrimEnd(')');$value=[double]::Parse($valueText,[Globalization.CultureInfo]::InvariantCulture);$dataType='Double'}else{$sourceId=[string]$fixtureItems[1]['source_item_id'];$vartype=11;$value=($match.Groups['variant'].Value -eq 'Bool(true)');$dataType='Boolean'}
  $entry=@($request['items']|Where-Object {$_['source_item_id'] -eq $sourceId})
  if($entry.Count -ne 1){throw 'source_fixture_catalog_mismatch'}
  $date=[DateTime]::FromFileTimeUtc($ticks)
  $samples+=@{item_id=[string]$entry[0]['item_id'];source_item_id=$sourceId;data_type=$dataType;value=$value;quality=$quality;source_timestamp_filetime=$ticks;source_timestamp=$date.ToString('o');hresult=0;source_vartype=$vartype;type_evidence='direct_COM_VARIANT';read_method='IOPCSyncIO::Read (Rust direct COM client)'}
 }
 if($samples.Count -ne 2){throw 'source_fixture_read_failed'}
 return $samples
}
if($LibraryOnly){return}
try{
 if($RequestBase64.Length -gt 65536){throw 'request_too_large'}
 $request=$json.DeserializeObject([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($RequestBase64)))
 $binding=$json.DeserializeObject([IO.File]::ReadAllText((Join-Path $root 'source-identity.json'),$utf8))
 $script:timestamp_policy='reject_unspecified'
 if($binding.ContainsKey('timestamp_policy')){$script:timestamp_policy=[string]$binding['timestamp_policy']}
 if(!(@('reject_unspecified','utc','local') -contains $script:timestamp_policy)){throw 'invalid_timestamp_policy'}
 $identity=Identity
 if($identity['hostname'] -ne $binding['expected_hostname'] -or !($identity['addresses'] -contains $binding['expected_address'])){throw 'identity_mismatch'}
 if($request['operation'] -eq 'discover'){
  $servers=@(Discover);Log ('DISCOVERY candidate_count='+$servers.Count+' view='+$identity['registry_view'])
  Output @{ok=$true;identity=$identity;servers=$servers};exit 0
 }
 if($request['operation'] -ne 'read'){throw 'operation_not_allowed'}
 if(!(@($binding['allowed_progids']) -contains $request['prog_id'])){throw 'progid_not_authorized'}
 if($request['transport'] -eq 'rust_da_com_fixture'){
  $samples=@(ReadRustDaFixture $request $binding)
  Output @{ok=$true;agent_revision='rust-direct-com-fixture-v1';identity=$identity;transport='rust_da_com_fixture';read_method='IOPCSyncIO::Read (Rust direct COM client)';samples=$samples};exit 0
 }
 $key=[Microsoft.Win32.Registry]::ClassesRoot.OpenSubKey($request['prog_id']+'\CLSID')
 if(!$key){throw 'progid_not_registered'}
 try{if([string]$key.GetValue('') -ne $request['clsid']){throw 'clsid_mismatch'}}finally{$key.Close()}
 if(@($request['items']).Count -lt 1 -or @($request['items']).Count -gt 128){throw 'invalid_item_count'}
 foreach($entry in $request['items']){if(!($entry['source_item_id'] -is [string]) -or !$entry['source_item_id'] -or $entry['source_item_id'].Length -gt 1024){throw 'invalid_item_id'}}
 $lab=$binding['lab_snapshot']
 if($lab -and $request['prog_id'] -eq [string]$lab['prog_id']){
  if(!$lab -or $lab['enabled'] -ne $true -or [string]$lab['clsid'] -ne [string]$request['clsid']){throw 'lab_snapshot_disabled'}
  . (Join-Path $root 'lab_snapshot_transport.ps1')
  Log ('LAB_SNAPSHOT_BEGIN port='+[int]$lab['port'])
  $result=Read-LabSnapshot $request $lab $json
  Log ('LAB_SNAPSHOT_OK pid='+$result['server_pid']+' session='+$result['server_session']+' count='+$result['samples'].Count)
  Output @{ok=$true;agent_revision='lab-snapshot-v1';identity=$identity;transport=$result['transport'];read_method=$result['read_method'];source_pid=$result['server_pid'];source_session=$result['server_session'];samples=$result['samples']}
  exit 0
 }
 if(!$Worker){
  # Enforce a wall-clock limit around COM; terminate only our own child worker.
  $outFile=Join-Path $root ('read-result-'+[Guid]::NewGuid().ToString('N')+'.json')
  $escaped=$MyInvocation.MyCommand.Path.Replace("'","''")
  $cmd="& '"+$escaped+"' -Worker -RequestBase64 '"+$RequestBase64+"' -ResultFile '"+$outFile+"'"
  $psi=New-Object Diagnostics.ProcessStartInfo
  $psi.FileName=Join-Path $PSHOME 'powershell.exe';$psi.Arguments='-STA -NoProfile -ExecutionPolicy Bypass -EncodedCommand '+[Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($cmd));$psi.UseShellExecute=$false;$psi.CreateNoWindow=$true
  $child=[Diagnostics.Process]::Start($psi)
  try{
   if(!$child.WaitForExit(10000)){$child.Kill();$child.WaitForExit();throw 'com_timeout'}
   if(!(Test-Path $outFile) -or (Get-Item $outFile).Length -gt 1048576){throw 'worker_failed'}
   [Console]::Out.WriteLine([IO.File]::ReadAllText($outFile,$utf8))
  }finally{if(Test-Path $outFile){Remove-Item -LiteralPath $outFile};$child.Close()}
  exit 0
 }
 $samples=@(ReadItems $request);Log ('READ progid='+$request['prog_id']+' items='+$samples.Count)
 Output @{ok=$true;agent_revision='group-sync-v5-four-scalar-types';identity=$identity;transport='opc_da_automation';samples=$samples};exit 0
}catch{
 $code=$_.Exception.Message
 if($code -notmatch '^(request_too_large|identity_mismatch|operation_not_allowed|progid_not_authorized|progid_not_registered|clsid_mismatch|invalid_item_count|invalid_item_id|invalid_timestamp_policy|com_timeout|worker_failed|lab_snapshot_disabled|lab_snapshot_unavailable|lab_snapshot_identity_mismatch|lab_snapshot_item_invalid|lab_snapshot_config_invalid|source_fixture_not_authorized|source_fixture_unavailable|source_fixture_hash_mismatch|source_fixture_catalog_mismatch|source_fixture_read_failed|source_fixture_cleanup_failed|source_fixture_cleanup_marker_present)$'){$code='source_error'}
 Log ('ERROR code='+$code)
 $hr=ErrorCode $_.Exception
 $phase=if($script:failure_phase){$script:failure_phase}else{$script:com_phase}
 Log ('ERROR_HRESULT='+$hr+' phase='+$phase+' exception_type='+$_.Exception.GetType().FullName)
 Output @{ok=$false;error_code=$code;hresult=$hr;diagnostic_stage=$phase;exception_type=$_.Exception.GetType().FullName};exit 1
}
