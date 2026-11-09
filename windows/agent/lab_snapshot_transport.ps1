# SPDX-License-Identifier: MIT
# Explicit, read-only transport for the Rust DA laboratory's existing instance.
# It is intentionally not a general OPC DA transport and never activates COM.

function Assert-LabSnapshotProcess($snapshot, $config, $process, $listeners) {
    if ($null -eq $snapshot -or $null -eq $config -or $null -eq $process) { throw 'lab_snapshot_identity_mismatch' }
    $pidValue = 0
    try { $pidValue = [int]$snapshot['pid'] } catch { throw 'lab_snapshot_identity_mismatch' }
    if ($pidValue -le 0 -or [int]$process['pid'] -ne $pidValue) { throw 'lab_snapshot_identity_mismatch' }
    if ([string]$process['path'] -ine [string]$config['expected_executable']) { throw 'lab_snapshot_identity_mismatch' }
    if ([string]$process['sha256'] -ine [string]$config['expected_sha256']) { throw 'lab_snapshot_identity_mismatch' }
    $port = [int]$config['port']
    if ($port -lt 1 -or $port -gt 65535 -or !(@($listeners) -contains $pidValue)) { throw 'lab_snapshot_identity_mismatch' }
    if ([string]$snapshot['machine_id'] -ne [string]$config['expected_machine_id']) { throw 'lab_snapshot_identity_mismatch' }
    if ([string]$snapshot['project_name'] -ne [string]$config['expected_project_name']) { throw 'lab_snapshot_identity_mismatch' }
    return $true
}

function Convert-LabSnapshotSamples($request, $config, $snapshot) {
    if ($snapshot['source_available'] -ne $true) { throw 'lab_snapshot_unavailable' }
    if ([string]$snapshot['machine_id'] -ne [string]$config['expected_machine_id'] -or
        [string]$snapshot['project_name'] -ne [string]$config['expected_project_name']) { throw 'lab_snapshot_identity_mismatch' }
    $allowed = @{}
    foreach ($entry in $config['items']) {
        if (!$entry['source_item_id'] -or !(@('Boolean','Double') -contains [string]$entry['data_type'])) { throw 'lab_snapshot_config_invalid' }
        $allowed[[string]$entry['source_item_id']] = [string]$entry['data_type']
    }
    $samples = @()
    foreach ($item in $request['items']) {
        $sourceId = [string]$item['source_item_id']
        $dataType = [string]$item['data_type']
        if (!$allowed.ContainsKey($sourceId) -or $allowed[$sourceId] -ne $dataType) { throw 'lab_snapshot_item_invalid' }
        $matches = @($snapshot['samples'] | Where-Object { [string]$_.item_id -ceq $sourceId })
        if ($matches.Count -ne 1) { throw 'lab_snapshot_item_invalid' }
        $sample = $matches[0]
        if ([string]$sample['data_type'] -cne $dataType) { throw 'lab_snapshot_item_invalid' }
        $value = $sample['value']
        $valueValid = $false
        if ($dataType -eq 'Boolean') {
            $valueValid = ($value -is [bool])
        } else {
            # JavaScriptSerializer 3.5 represents JSON decimal numbers as
            # System.Decimal. Accept only CLR numeric TypeCodes, then convert
            # invariantly; strings and Boolean values are never coerced.
            $typeCode = -1
            if ($null -ne $value -and $value -is [ValueType]) { $typeCode = [int][Type]::GetTypeCode($value.GetType()) }
            if ($typeCode -ge 5 -and $typeCode -le 15) {
                try { $value = [Convert]::ToDouble($value, [Globalization.CultureInfo]::InvariantCulture); $valueValid = ![double]::IsNaN($value) -and ![double]::IsInfinity($value) } catch { $valueValid = $false }
            }
        }
        $quality = 0
        try { $quality = [int]$sample['quality'] } catch { throw 'lab_snapshot_item_invalid' }
        if ($quality -lt 0 -or $quality -gt 65535) { throw 'lab_snapshot_item_invalid' }
        $timestamp = $null
        if ($null -ne $sample['source_timestamp_filetime']) {
            try {
                $candidate = [long]$sample['source_timestamp_filetime']
                if ($candidate -gt 0 -and $candidate -le 2650467743999999999) { $timestamp = $candidate }
            } catch { $timestamp = $null }
        }
        $hresult = 0
        if (!$valueValid) { $hresult = -2147023174; $quality = 8; $value = $null; $timestamp = $null }
        elseif (!$timestamp -and ($quality -band 192) -eq 192) { $quality = 64 }
        $variant = if ($dataType -eq 'Boolean') { 11 } else { 5 }
        $samples += @{item_id=[string]$item['item_id'];source_item_id=$sourceId;data_type=$dataType;
            value=$value;quality=$quality;hresult=$hresult;source_timestamp_filetime=$timestamp;
            source_vartype=$variant;type_evidence='rust_runtime_snapshot_declared_type';
            read_method='rust_lab_loopback_snapshot';hresult_scope='snapshot_http'}
    }
    return ,$samples
}

function Read-LabSnapshot($request, $config, $json) {
    if ($null -eq $config -or $config['enabled'] -ne $true -or [string]$config['prog_id'] -ne 'RustDaBridge.Lab.1') { throw 'lab_snapshot_disabled' }
    $port = 0
    try { $port = [int]$config['port'] } catch { throw 'lab_snapshot_config_invalid' }
    if ($port -lt 1 -or $port -gt 65535 -or !([string]$config['expected_executable']) -or
        !([string]$config['expected_machine_id']) -or !([string]$config['expected_project_name'])) { throw 'lab_snapshot_config_invalid' }
    if ([string]$config['expected_sha256'] -notmatch '^[0-9a-fA-F]{64}$') { throw 'lab_snapshot_config_invalid' }
    $url = 'http://127.0.0.1:' + $port + '/v1/snapshot'
    $web = $null; $reader = $null; $response = $null
    try {
        $web = [Net.HttpWebRequest]::Create($url); $web.Proxy = $null; $web.Timeout = 2000; $web.ReadWriteTimeout = 2000
        $response = $web.GetResponse()
        $reader = New-Object IO.StreamReader($response.GetResponseStream(), [Text.Encoding]::UTF8)
        $text = $reader.ReadToEnd()
        if ($text.Length -gt 2097152) { throw 'lab_snapshot_unavailable' }
        $snapshot = $json.DeserializeObject($text)
    } catch { throw 'lab_snapshot_unavailable' }
    finally { if ($reader) { $reader.Close() }; if ($response) { $response.Close() } }
    $pidValue = 0
    try { $pidValue = [int]$snapshot['pid'] } catch { throw 'lab_snapshot_identity_mismatch' }
    $process = $null
    try {
        $proc = Get-WmiObject Win32_Process -Filter ('ProcessId = ' + $pidValue)
        if ($proc) {
            $sha = [Security.Cryptography.SHA256]::Create(); $file = [IO.File]::OpenRead([string]$proc.ExecutablePath)
            try { $digest = [BitConverter]::ToString($sha.ComputeHash($file)).Replace('-','').ToLowerInvariant() }
            finally { $file.Close(); $sha.Clear() }
            $process = @{pid=[int]$proc.ProcessId;path=[string]$proc.ExecutablePath;session=[int]$proc.SessionId;sha256=$digest}
        }
    } catch {}
    $portPattern = '^\s*TCP\s+127\.0\.0\.1:' + [regex]::Escape([string]$port) + '\s+0\.0\.0\.0:0\s+LISTENING\s+(\d+)\s*$'
    $listeners = @()
    foreach ($line in @(& netstat.exe -ano -p tcp)) { if ([string]$line -match $portPattern) { $listeners += [int]$matches[1] } }
    Assert-LabSnapshotProcess $snapshot $config $process $listeners | Out-Null
    $samples = Convert-LabSnapshotSamples $request $config $snapshot
    return @{samples=$samples;server_pid=$pidValue;server_session=$process['session'];
        transport='rust_lab_loopback_snapshot';read_method='GET /v1/snapshot over SSH-local loopback'}
}
