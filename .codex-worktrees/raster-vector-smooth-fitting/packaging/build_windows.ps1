param(
    [switch]$MachineSeed,
    [string]$ConfigPath = ".\config\network-local.json",
    [string]$CalibrationProfile =
        ".\data\calibration_profiles\1920x1080-manual-focus-010",
    [string]$MachineRegistryPath = "",
    [string]$BridgeToken = $env:E3_BRIDGE_TOKEN,
    [string]$Revision = $env:GITHUB_SHA,
    [string]$Channel = "development",
    [string]$Repository = "lukelave-boop/E3",
    [string]$ReleaseTag = "e3-development",
    [string]$OutputBaseFilename = "E3-Setup"
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $repoRoot
$python = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    $python = (Get-Command python -ErrorAction Stop).Source
}
if ([string]::IsNullOrWhiteSpace($Revision)) {
    $Revision = (git rev-parse HEAD).Trim()
}
if ([string]::IsNullOrWhiteSpace($Revision)) {
    throw "Could not determine the E3 build revision"
}

& $python -m pip install --upgrade pyinstaller
if ($LASTEXITCODE -ne 0) {
    throw "Could not install PyInstaller"
}

$version = (
    & $python .\packaging\version_for_build.py
).Trim()
if ([string]::IsNullOrWhiteSpace($version)) {
    throw "Could not determine the E3 application version"
}
$buildInfo = Join-Path $repoRoot "build-info.json"
& $python .\packaging\write_build_info.py `
    --output $buildInfo `
    --revision $Revision `
    --channel $Channel `
    --repository $Repository `
    --release-tag $ReleaseTag `
    --platform-key windows-x86_64
if ($LASTEXITCODE -ne 0) {
    throw "Could not generate build-info.json"
}

Remove-Item .\build\E3 -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item .\dist\E3 -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item .\installer-dist -Recurse -Force -ErrorAction SilentlyContinue

& $python -m PyInstaller `
    --noconfirm `
    --clean `
    --windowed `
    --onedir `
    --name E3 `
    --collect-all laser_aligner `
    --collect-all cv2 `
    --collect-all PySide6 `
    .\packaging\e3_entry.py
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller build failed"
}

Copy-Item $buildInfo .\dist\E3\build-info.json -Force
New-Item .\dist\E3\config -ItemType Directory -Force | Out-Null
Copy-Item .\config\default.json .\dist\E3\config\default.json -Force

$seedRoot = Join-Path $PSScriptRoot "machine-seed"
Remove-Item $seedRoot -Recurse -Force -ErrorAction SilentlyContinue
if ($MachineSeed) {
    if (-not (Test-Path $ConfigPath)) {
        throw "Machine configuration does not exist: $ConfigPath"
    }
    if (-not (Test-Path $CalibrationProfile)) {
        throw "Calibration profile does not exist: $CalibrationProfile"
    }
    if ([string]::IsNullOrWhiteSpace($BridgeToken)) {
        $preservedToken = Join-Path $env:LOCALAPPDATA `
            "E3 Positioning System\secrets\bridge-token.txt"
        if (Test-Path $preservedToken) {
            $BridgeToken = (Get-Content $preservedToken -Raw).Trim()
        }
    }
    if ([string]::IsNullOrWhiteSpace($BridgeToken)) {
        throw "Machine-seed builds require E3_BRIDGE_TOKEN or -BridgeToken"
    }

    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    $seedConfigDir = Join-Path $seedRoot "config"
    $seedDataDir = Join-Path $seedRoot "data\calibration_profiles"
    $seedSecretsDir = Join-Path $seedRoot "secrets"
    New-Item $seedConfigDir -ItemType Directory -Force | Out-Null
    New-Item $seedDataDir -ItemType Directory -Force | Out-Null
    New-Item $seedSecretsDir -ItemType Directory -Force | Out-Null

    $config = Get-Content $ConfigPath -Raw | ConvertFrom-Json
    $config.app.data_dir = "../data"
    $configText = $config | ConvertTo-Json -Depth 100
    [IO.File]::WriteAllText(
        (Join-Path $seedConfigDir "network-local.json"),
        $configText + "`n",
        $utf8NoBom
    )
    Copy-Item $CalibrationProfile $seedDataDir -Recurse -Force

    if ([string]::IsNullOrWhiteSpace($MachineRegistryPath)) {
        $stateRoot = Split-Path (Split-Path $ConfigPath -Parent) -Parent
        $MachineRegistryPath = Join-Path $stateRoot "data\machines.json"
    }
    if (-not (Test-Path $MachineRegistryPath)) {
        throw "Machine-seed builds require a saved machine registry: $MachineRegistryPath"
    }
    Copy-Item `
        $MachineRegistryPath `
        (Join-Path $seedRoot "data\machines.json") `
        -Force

    [IO.File]::WriteAllText(
        (Join-Path $seedSecretsDir "bridge-token.txt"),
        $BridgeToken.Trim(),
        $utf8NoBom
    )
}

$innoCandidates = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
)
$iscc = $innoCandidates |
    Where-Object { Test-Path $_ } |
    Select-Object -First 1
if ([string]::IsNullOrWhiteSpace($iscc)) {
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        winget install `
            --id JRSoftware.InnoSetup `
            --exact `
            --accept-source-agreements `
            --accept-package-agreements
    } elseif (Get-Command choco -ErrorAction SilentlyContinue) {
        choco install innosetup -y --no-progress
    } else {
        throw "Inno Setup 6 is required"
    }
    $iscc = $innoCandidates |
        Where-Object { Test-Path $_ } |
        Select-Object -First 1
}
if ([string]::IsNullOrWhiteSpace($iscc)) {
    throw "Could not locate Inno Setup ISCC.exe"
}

$defines = @(
    "/DMyAppVersion=$version",
    "/DOutputBaseFilename=$OutputBaseFilename"
)
if ($MachineSeed) {
    $defines += "/DMachineSeed=1"
}
& $iscc @defines .\packaging\E3-installer.iss
if ($LASTEXITCODE -ne 0) {
    throw "Inno Setup compilation failed"
}

Write-Host ""
Write-Host "E3 Windows installer complete:"
Write-Host (Resolve-Path ".\installer-dist\$OutputBaseFilename.exe")
