# SPDX-License-Identifier: MIT
# User-scope COM registration for the isolated Rust DA source fixture only.
param(
    [string]$LaboratoryDirectory = 'D:\OPCB\rust-da-source-lab-20261106-build5',
    [string]$BackupDirectory = 'D:\OPCB\rust-da-source-lab-20261106-build5\before-integration',
    [string]$ExpectedSha256 = 'f2bcb996b1c0c768cbb4f36f7bb0a5cbca4a96d52b9bdc7e828e90338a1e7398',
    [int]$ExpectedDestinationPid = 5780
)
$ErrorActionPreference = 'Stop'
$progId = 'RustDaBridge.SourceLab.1'
$clsid = '{C7482207-6725-496D-B846-A0B31B941937}'
$da2Category = '{63D5F432-CFE4-11D1-B2C8-0060083BA1FB}'
$exe = Join-Path $LaboratoryDirectory 'rust-da-com-lab.exe'
$destinationExe = 'D:\OPCB\rust-da-agent-lab-20261106-hotupdate-build2\rust-da-com-lab.exe'
$backupManifest = Join-Path $BackupDirectory 'source-registration.txt'

if ($env:COMPUTERNAME -ne 'RB1PI2') { throw 'Unexpected host; expected RB1PI2.' }
if (!(Test-Path $BackupDirectory)) { throw 'Pre-registration backup directory is missing.' }
if (Test-Path $backupManifest) { throw 'Registration record already exists; refusing to run twice.' }
if (Test-Path (Join-Path $LaboratoryDirectory 'project.json')) { throw 'Refusing to register the source fixture from a destination project folder.' }
if (!(Test-Path $exe)) { throw 'Source fixture executable is missing.' }
$sha = [Security.Cryptography.SHA256]::Create()
$actualSha256 = [BitConverter]::ToString($sha.ComputeHash([IO.File]::ReadAllBytes($exe))).Replace('-', '').ToLowerInvariant()
if ($actualSha256 -ne $ExpectedSha256.ToLowerInvariant()) { throw 'Source fixture SHA-256 mismatch.' }

$destination = Get-WmiObject Win32_Process -Filter ('ProcessId=' + $ExpectedDestinationPid)
if (!$destination -or $destination.ExecutablePath -ne $destinationExe -or $destination.CommandLine -notmatch '-Embedding') {
    throw 'Destination process identity changed; refusing registration.'
}

$progKey = 'Software\Classes\' + $progId
$clsidKey = 'Software\Classes\CLSID\' + $clsid
foreach ($root in @('HKCU:\Software\Classes', 'HKLM:\Software\Classes')) {
    if ((Test-Path ($root + '\' + $progId)) -or (Test-Path ($root + '\CLSID\' + $clsid))) {
        throw ('ProgID/CLSID conflict in ' + $root)
    }
}

function Set-DefaultValue([string]$path, [string]$value) {
    $key = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey($path)
    if (!$key) { throw ('Could not create HKCU key: ' + $path) }
    try { $key.SetValue('', $value, [Microsoft.Win32.RegistryValueKind]::String) }
    finally { $key.Close() }
}

try {
    Set-DefaultValue $progKey 'Rust DA Source Lab (simulated)'
    Set-DefaultValue ($progKey + '\CLSID') $clsid
    Set-DefaultValue $clsidKey 'Rust DA Source Lab (simulated)'
    Set-DefaultValue ($clsidKey + '\ProgID') $progId
    Set-DefaultValue ($clsidKey + '\LocalServer32') ('"' + $exe + '" --source-lab-server')
    $category = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey($clsidKey + '\Implemented Categories\' + $da2Category)
    if (!$category) { throw 'Could not create the OPC DA 2.0 category.' }
    $category.Close()

    $check = [Microsoft.Win32.Registry]::ClassesRoot.OpenSubKey($progId + '\CLSID')
    if (!$check -or [string]$check.GetValue('') -ne $clsid) { throw 'HKCR ProgID mapping verification failed.' }
    $check.Close()
    $check = [Microsoft.Win32.Registry]::ClassesRoot.OpenSubKey('CLSID\' + $clsid + '\LocalServer32')
    if (!$check -or [string]$check.GetValue('') -ne ('"' + $exe + '" --source-lab-server')) { throw 'HKCR LocalServer32 verification failed.' }
    $check.Close()
    $check = [Microsoft.Win32.Registry]::ClassesRoot.OpenSubKey('CLSID\' + $clsid + '\Implemented Categories\' + $da2Category)
    if (!$check) { throw 'HKCR OPC DA 2.0 category verification failed.' }
    $check.Close()
} catch {
    # The preflight confirmed both trees were absent. Remove only these two new
    # user-scope trees if a partial registration fails.
    foreach ($path in @('HKCU:\' + $progKey, 'HKCU:\' + $clsidKey)) {
        if (Test-Path $path) { Remove-Item -LiteralPath $path -Recurse -Force }
    }
    throw
}

$lines = @(
    'scope=HKCU\Software\Classes',
    ('progid=' + $progId),
    ('clsid=' + $clsid),
    ('localserver32="' + $exe + '" --source-lab-server'),
    ('category=' + $da2Category),
    ('executable_sha256=' + $actualSha256),
    'rollback=remove only the new HKCU ProgID and CLSID trees'
)
[IO.File]::WriteAllLines($backupManifest, $lines, (New-Object Text.UTF8Encoding($false)))
Write-Output ('SOURCE_COM_REGISTRATION_PASS user=' + $env:USERDOMAIN + '\' + $env:USERNAME + ' sha256=' + $actualSha256)
Write-Output ('PROGID=' + $progId + ' CLSID=' + $clsid + ' CATID_DA2=' + $da2Category)
Write-Output ('LOCALSERVER32="' + $exe + '" --source-lab-server')
Write-Output 'SCOPE=HKCU_ONLY; HKLM_AND_DESTINATION_CLSID_UNCHANGED'
