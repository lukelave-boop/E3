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
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$')]
    [string]$OutputBaseFilename = "E3-Setup",
    [string]$RetentionRegistry = '',
    [switch]$RequireFreshOutputs,
    [switch]$EphemeralCI
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
function Assert-FreshBuildPath {
    param([string]$Path, [switch]$AllowExistingLeaf)
    $current = [IO.Path]::GetFullPath($Path)
    $leaf = $current
    while (-not [string]::IsNullOrEmpty($current)) {
        try {
            $item = Get-Item -Force -LiteralPath $current -ErrorAction Stop
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "Fresh-output build refuses a reparse point: $current"
            }
            if ($current -eq $leaf -and -not $AllowExistingLeaf) {
                throw "Fresh-output build refuses to replace: $current"
            }
        } catch [System.Management.Automation.ItemNotFoundException] {
            # A new output may be absent; inspect its existing ancestors too.
        }
        $parent = [IO.Directory]::GetParent($current)
        if ($null -eq $parent) { break }
        $current = $parent.FullName
    }
}
if ($RequireFreshOutputs) {
    if ($MachineSeed) {
        throw "Fresh-output retention builds do not support private machine seeds"
    }
    foreach ($relative in @("build\E3", "dist\E3", "installer-dist", "packaging\machine-seed", "build-info.json", "E3.spec")) {
        Assert-FreshBuildPath -Path (Join-Path $repoRoot $relative)
    }
}
Set-Location $repoRoot
$python = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    $python = (Get-Command python -ErrorAction Stop).Source
}
. (Join-Path $PSScriptRoot "build_storage_gate.ps1")
$storageMode = if ($MachineSeed -or $Channel -eq "home") { "private" } else { "windows" }
$storageLease = Start-E3BuildStorage -Python $python -Worktree $repoRoot `
    -Mode $storageMode -InstallerName $OutputBaseFilename `
    -RetentionRegistry $RetentionRegistry -EphemeralCI:$EphemeralCI
if ([string]::IsNullOrWhiteSpace($Revision)) {
    $Revision = (git rev-parse HEAD).Trim()
}
if ([string]::IsNullOrWhiteSpace($Revision)) {
    throw "Could not determine the E3 build revision"
}

$originalPath = $env:PATH
$sanitizedPath = & $python .\packaging\windows_build_guard.py `
    sanitize-path `
    --system-root $env:WINDIR
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($sanitizedPath)) {
    throw "Could not establish a collision-free Windows build PATH"
}
$env:PATH = $sanitizedPath.Trim()

try {
    & $python -m pip install --upgrade pyinstaller
    if ($LASTEXITCODE -ne 0) {
        throw "Could not install PyInstaller"
    }

    & $python -c "from PySide6 import QtCore, QtGui, QtWidgets"
    if ($LASTEXITCODE -ne 0) {
        throw "PySide6 does not import under the sanitized Windows build PATH"
    }

    $version = (
        & $python .\packaging\version_for_build.py
    ).Trim()
    if ([string]::IsNullOrWhiteSpace($version)) {
        throw "Could not determine the E3 application version"
    }
    $buildInfo = Join-Path $repoRoot "build-info.json"
    $seedRoot = Join-Path $PSScriptRoot "machine-seed"
    # Dependencies can take time. Refresh protection and ownership immediately
    # before replacing this build's exact previously generated outputs.
    Confirm-E3BuildReplacement -Python $python -Lease $storageLease
    if ($RequireFreshOutputs) {
        foreach ($relative in @("build\E3", "dist\E3", "installer-dist", "packaging\machine-seed", "build-info.json", "E3.spec")) {
            Assert-FreshBuildPath -Path (Join-Path $repoRoot $relative)
        }
    } else {
        foreach ($relative in @("build\E3", "dist\E3", "installer-dist\$OutputBaseFilename.exe", "packaging\machine-seed", "build-info.json", "E3.spec")) {
            $target = [IO.Path]::GetFullPath((Join-Path $repoRoot $relative))
            if (Test-Path -LiteralPath $target) {
                Remove-Item -LiteralPath $target -Recurse -Force -ErrorAction Stop
            }
        }
    }
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

    if ($RequireFreshOutputs) {
        foreach ($relative in @("build\E3", "dist\E3", "installer-dist", "E3.spec")) {
            Assert-FreshBuildPath -Path (Join-Path $repoRoot $relative)
        }
    }

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

    & $python .\packaging\windows_build_guard.py `
        validate-bundle `
        --internal-root .\dist\E3\_internal
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller collected conflicting foreign runtime DLLs"
    }
}
finally {
    $env:PATH = $originalPath
}

if ($RequireFreshOutputs) {
    $bundleInfo = Join-Path $repoRoot "dist\E3\build-info.json"
    $bundleConfig = Join-Path $repoRoot "dist\E3\config"
    Assert-FreshBuildPath -Path $bundleInfo
    [IO.File]::Copy($buildInfo, $bundleInfo, $false)
    Assert-FreshBuildPath -Path $bundleConfig
    New-Item -Path $bundleConfig -ItemType Directory -ErrorAction Stop | Out-Null
    $defaultConfig = Join-Path $bundleConfig "default.json"
    Assert-FreshBuildPath -Path $defaultConfig
    [IO.File]::Copy((Join-Path $repoRoot "config\default.json"), $defaultConfig, $false)
} else {
    Copy-Item $buildInfo .\dist\E3\build-info.json -Force
    New-Item .\dist\E3\config -ItemType Directory -Force | Out-Null
    Copy-Item .\config\default.json .\dist\E3\config\default.json -Force
}

$seedRoot = Join-Path $PSScriptRoot "machine-seed"
if ($RequireFreshOutputs) {
    Assert-FreshBuildPath -Path $seedRoot
}
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
if ($RequireFreshOutputs) {
    Assert-FreshBuildPath -Path (Join-Path $repoRoot "installer-dist")
}
& $iscc @defines .\packaging\E3-installer.iss
if ($LASTEXITCODE -ne 0) {
    throw "Inno Setup compilation failed"
}
Complete-E3BuildStorage -Python $python -Lease $storageLease

Write-Host ""
Write-Host "E3 Windows installer complete:"
Write-Host (Resolve-Path ".\installer-dist\$OutputBaseFilename.exe")
