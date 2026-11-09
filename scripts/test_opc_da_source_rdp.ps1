# SPDX-License-Identifier: MIT
# Run only in the RB1PI2 RDP laboratory session containing our PI OPC Client.
param([string]$AgentDirectory='')
if(!$AgentDirectory){$AgentDirectory=Split-Path -Parent $MyInvocation.MyCommand.Path}
$ErrorActionPreference='Stop'
if([string](Get-WmiObject Win32_ComputerSystem).Name -ne 'RB1PI2'){throw 'Laboratory host mismatch'}
$request='{"operation":"read","prog_id":"RustDaBridge.Lab.1","clsid":"{7A2F45B1-64D3-4E8C-9A11-3C852B7F2140}","items":[{"source_item_id":"RB1PI1.TESTE.TESTE1.TESTE2","item_id":"DiscoveryLab.Number","data_type":"Double"},{"source_item_id":"RB1PI1.TESTE.TESTE1.DIGITAL","item_id":"DiscoveryLab.Boolean","data_type":"Boolean"}]}'
$payload=[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($request))
$result=Join-Path $AgentDirectory ('rdp-source-result-'+[Guid]::NewGuid().ToString('N')+'.json')
$lines=@(& (Join-Path $PSHOME 'powershell.exe') -STA -NoProfile -ExecutionPolicy Bypass -File (Join-Path $AgentDirectory 'opc_da_source_agent.ps1') -RequestBase64 $payload)
[IO.File]::WriteAllText($result,($lines -join "`n"),(New-Object Text.UTF8Encoding($false)))
'SOURCE_RDP_RESULT='+$result
$lines
