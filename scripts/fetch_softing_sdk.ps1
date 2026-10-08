[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$repoUrl = 'https://github.com/SoftingIndustrial/OPC-Classic-SDK.git'
$expected = '08f4b3e968acfce4b775299741cef1819e24fda1'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$vendorRoot = Join-Path $projectRoot '.vendor'
$target = Join-Path $vendorRoot 'softing-opc-classic-sdk'

if (-not (Test-Path -LiteralPath $vendorRoot)) { New-Item -ItemType Directory -Path $vendorRoot | Out-Null }
if (Test-Path -LiteralPath $target) {
    if (-not (Test-Path -LiteralPath (Join-Path $target '.git'))) { throw "Existing target is not a Git checkout: $target" }
    $actualUrl = (& git -C $target remote get-url origin).Trim()
    if ($LASTEXITCODE -ne 0 -or $actualUrl -ne $repoUrl) { throw "Existing checkout is not the official repository: $actualUrl" }
    $actual = (& git -C $target rev-parse HEAD).Trim()
    if ($LASTEXITCODE -ne 0 -or $actual -ne $expected) { throw "Existing checkout has commit '$actual'; expected '$expected'. Refusing to modify it." }
    $dirty = (& git -C $target status --porcelain).Trim()
    if ($LASTEXITCODE -ne 0 -or $dirty) { throw 'Existing pinned SDK checkout has local changes; refusing to use or modify it.' }
    Write-Output "Softing SDK already verified at $actual"
    exit 0
}

& git clone --no-checkout $repoUrl $target
if ($LASTEXITCODE -ne 0) { throw 'git clone failed.' }
& git -C $target checkout --detach $expected
if ($LASTEXITCODE -ne 0) { throw "Checkout of pinned commit $expected failed." }
$actual = (& git -C $target rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $actual -ne $expected) { throw "SDK SHA validation failed: '$actual'." }
Write-Output "Softing SDK verified at $actual in ignored .vendor directory."
