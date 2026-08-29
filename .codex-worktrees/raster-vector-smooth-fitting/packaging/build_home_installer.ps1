param(
    [string]$StateRoot = "",
    [string]$OutputBaseFilename = "E3-Home-Setup"
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $repoRoot

if ([string]::IsNullOrWhiteSpace($StateRoot)) {
    if ($env:E3_USER_STATE_DIR) {
        $StateRoot = $env:E3_USER_STATE_DIR
    } elseif ($env:LOCALAPPDATA) {
        $StateRoot = Join-Path $env:LOCALAPPDATA "E3 Positioning System"
    } else {
        throw "Could not determine the E3 user-state directory"
    }
}
$StateRoot = (Resolve-Path $StateRoot).Path

$configPath = Join-Path $StateRoot "config\network-local.json"
$tokenPath = Join-Path $StateRoot "secrets\bridge-token.txt"
$machinesPath = Join-Path $StateRoot "data\machines.json"

if (-not (Test-Path $configPath)) {
    throw "Current E3 machine configuration does not exist: $configPath"
}
if (-not (Test-Path $tokenPath)) {
    throw "Current E3 bridge credential does not exist: $tokenPath"
}
if (-not (Test-Path $machinesPath)) {
    throw "Current E3 machine registry does not exist: $machinesPath"
}

$config = Get-Content -Raw -LiteralPath $configPath | ConvertFrom-Json
$controls = $config.camera.controls
$automatic = 0
if ($controls.PSObject.Properties.Name -contains "focus_automatic_continuous") {
    $automatic = $controls.focus_automatic_continuous
} elseif ($controls.PSObject.Properties.Name -contains "focus_auto") {
    $automatic = $controls.focus_auto
}
$focusLabel = "autofocus"
if (-not ($automatic -eq 1 -or $automatic -eq $true)) {
    $focus = 0
    if ($controls.PSObject.Properties.Name -contains "focus_absolute") {
        $focus = [int]$controls.focus_absolute
    }
    $focusLabel = "manual-focus-{0:D3}" -f $focus
}
$profileKey = "{0}x{1}-{2}" -f [int]$config.camera.width, [int]$config.camera.height, $focusLabel
$calibrationProfile = Join-Path $StateRoot ("data\calibration_profiles\" + $profileKey)
if (-not (Test-Path $calibrationProfile)) {
    throw "Current calibration profile does not exist: $calibrationProfile"
}

$token = (Get-Content -Raw -LiteralPath $tokenPath).Trim()
if ($token.Length -lt 24) {
    throw "Current E3 bridge credential is invalid"
}

Write-Host ""
Write-Host "Building PRIVATE preconfigured E3 home installer" -ForegroundColor Cyan
Write-Host "================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Machine config:     $configPath"
Write-Host "Machine registry:   $machinesPath"
Write-Host "Calibration profile:$calibrationProfile"
Write-Host ""
Write-Host "This installer contains the household bridge credential." -ForegroundColor Yellow
Write-Host "Do not publish it as a public GitHub release artifact." -ForegroundColor Yellow
Write-Host ""

& (Join-Path $PSScriptRoot "build_windows.ps1") `
    -MachineSeed `
    -ConfigPath $configPath `
    -CalibrationProfile $calibrationProfile `
    -MachineRegistryPath $machinesPath `
    -BridgeToken $token `
    -Channel "home" `
    -OutputBaseFilename $OutputBaseFilename

if ($LASTEXITCODE -ne 0) {
    throw "The preconfigured E3 home installer build failed"
}

Write-Host ""
Write-Host "Private home installer complete:" -ForegroundColor Green
Write-Host (Resolve-Path ".\installer-dist\$OutputBaseFilename.exe")
