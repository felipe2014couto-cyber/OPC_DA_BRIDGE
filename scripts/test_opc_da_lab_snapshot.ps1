param([string]$HelperPath=(Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) '..\windows\agent\lab_snapshot_transport.ps1'))
$ErrorActionPreference='Stop'
. ([IO.Path]::GetFullPath($HelperPath))
$checks=0
function Assert($condition,[string]$message){if(!$condition){throw ('ASSERT: '+$message)};$script:checks++}
function MustFail($scriptBlock,[string]$message){$failed=$false;try{& $scriptBlock}catch{$failed=$true};Assert $failed $message}
$config=@{enabled=$true;prog_id='RustDaBridge.Lab.1';clsid='{7A2F45B1-64D3-4E8C-9A11-3C852B7F2140}';port=49321;
 expected_executable='D:\OPCB\rust-da-agent-lab\rust-da-com-lab.exe';expected_sha256=('a'*64);expected_machine_id='733cc9b5-7837-44f6-8ef8-a222b1ee6ddd';
 expected_project_name='RB1PI1';items=@(
  @{source_item_id='RB1PI1.TESTE.TESTE1.TESTE2';data_type='Double'},
  @{source_item_id='RB1PI1.TESTE.TESTE1.DIGITAL';data_type='Boolean'})}
$snapshot=@{pid=7531;machine_id=$config.expected_machine_id;project_name=$config.expected_project_name;
 configuration_revision=19;source_available=$true;samples=@(
  @{item_id='RB1PI1.TESTE.TESTE1.TESTE2';data_type='Double';value=[decimal]7.0;quality=192;source_timestamp_filetime=[long]134384620599423840},
  @{item_id='RB1PI1.TESTE.TESTE1.DIGITAL';data_type='Boolean';value=$false;quality=192;source_timestamp_filetime=[long]134384620599423840})}
$process=@{pid=7531;path=$config.expected_executable;session=2;sha256=$config.expected_sha256}
Assert (Assert-LabSnapshotProcess $snapshot $config $process @(7531)) 'dynamic PID/path/session accepted'
MustFail {Assert-LabSnapshotProcess $snapshot $config @{pid=7532;path=$config.expected_executable;session=0} @(7531)} 'PID mismatch rejected'
MustFail {Assert-LabSnapshotProcess $snapshot $config @{pid=7531;path='D:\other.exe';session=2;sha256=$config.expected_sha256} @(7531)} 'image path mismatch rejected'
MustFail {Assert-LabSnapshotProcess $snapshot $config $process @(7532)} 'listener owner mismatch rejected'
$request=@{items=@(
 @{source_item_id='RB1PI1.TESTE.TESTE1.TESTE2';item_id='DiscoveryLab.Number';data_type='Double'},
 @{source_item_id='RB1PI1.TESTE.TESTE1.DIGITAL';item_id='DiscoveryLab.Boolean';data_type='Boolean'})}
$samples=Convert-LabSnapshotSamples $request $config $snapshot
Assert ($samples.Count -eq 2) 'both samples mapped'
Assert ($samples[0].value -eq 7.0 -and $samples[0].source_vartype -eq 5) 'Double type/value mapping'
Assert ($samples[1].value -eq $false -and $samples[1].source_vartype -eq 11) 'Boolean false preserved'
Assert ($samples[0].quality -eq 192 -and $samples[0].hresult -eq 0) 'Good and successful snapshot response'
Assert ($samples[0].source_timestamp_filetime -eq [long]134384620599423840) 'FILETIME copied without conversion'
Assert ($samples[0].hresult_scope -eq 'snapshot_http' -and $samples[0].read_method -eq 'rust_lab_loopback_snapshot') 'transport explicitly identified'
$withoutTimestamp=$snapshot.Clone();$withoutTimestamp.samples=@($snapshot.samples);$withoutTimestamp.samples[0]=@{item_id='RB1PI1.TESTE.TESTE1.TESTE2';data_type='Double';value=[decimal]7.0;quality=192;source_timestamp_filetime=$null}
$uncertain=Convert-LabSnapshotSamples @{items=@($request.items[0])} $config $withoutTimestamp
Assert ($null -eq $uncertain[0].source_timestamp_filetime -and $uncertain[0].quality -eq 64) 'missing timestamp stays absent and downgrades Good'
$offline=$snapshot.Clone();$offline['source_available']=$false
MustFail {Convert-LabSnapshotSamples $request $config $offline} 'unavailable runtime rejected'
$wrongMachine=$snapshot.Clone();$wrongMachine['machine_id']='other-machine'
MustFail {Convert-LabSnapshotSamples $request $config $wrongMachine} 'wrong runtime identity rejected'
$wrongItem=@{items=@(@{source_item_id='Other.Item';item_id='DiscoveryLab.Number';data_type='Double'})}
MustFail {Convert-LabSnapshotSamples $wrongItem $config $snapshot} 'unallowlisted source item rejected'
$wrongType=@{items=@(@{source_item_id='RB1PI1.TESTE.TESTE1.TESTE2';item_id='DiscoveryLab.Number';data_type='Boolean'})}
MustFail {Convert-LabSnapshotSamples $wrongType $config $snapshot} 'type mismatch rejected'
$badValue=$snapshot.Clone();$badValue.samples=@($snapshot.samples);$badValue.samples[0]=@{item_id='RB1PI1.TESTE.TESTE1.TESTE2';data_type='Double';value='7';quality=192;source_timestamp_filetime=134384620599423840}
$badSamples=Convert-LabSnapshotSamples @{items=@($request.items[0])} $config $badValue
Assert ($badSamples[0].hresult -lt 0 -and $badSamples[0].quality -eq 8 -and $null -eq $badSamples[0].value) 'wrong runtime value is Bad, not coerced'
Write-Output ('LAB_SNAPSHOT_MODEL_PASS checks='+$checks)
