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
    [switch]$EphemeralCI,
    [switch]$PassThru
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$sourceRoot = $repoRoot
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
Set-Location $sourceRoot
$python = Join-Path $sourceRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    $python = (Get-Command python -ErrorAction Stop).Source
}
$ConfigPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($ConfigPath)
$CalibrationProfile = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($CalibrationProfile)
if (-not [string]::IsNullOrWhiteSpace($MachineRegistryPath)) {
    $MachineRegistryPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($MachineRegistryPath)
}
. (Join-Path $PSScriptRoot "build_storage_gate.ps1")
Assert-E3BuildDependencies -Python $python -Modules @('PyInstaller', 'PySide6')
$innoCandidates = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
)
$iscc = $innoCandidates |
    Where-Object { Test-Path $_ } |
    Select-Object -First 1
if ([string]::IsNullOrWhiteSpace($iscc)) {
    throw "Inno Setup 6 must already be installed in Program Files or LocalAppData\Programs. Install it explicitly before retrying; this build does not run winget or Chocolatey."
}
$preflightBytecode = [Environment]::GetEnvironmentVariable('PYTHONDONTWRITEBYTECODE', 'Process')
try {
    $env:PYTHONDONTWRITEBYTECODE = '1'
    $sanitizedPath = & $python .\packaging\windows_build_guard.py `
        sanitize-path `
        --system-root $env:WINDIR
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($sanitizedPath)) {
        throw "Could not establish a collision-free Windows build PATH"
    }
} finally {
    Restore-E3EnvironmentValue -Name 'PYTHONDONTWRITEBYTECODE' -Value $preflightBytecode
}
$storageMode = if ($MachineSeed -or $Channel -eq "home") { "private" } else { "windows" }
$storageLease = Start-E3BuildStorage -Python $python -Worktree $repoRoot `
    -Mode $storageMode -InstallerName $OutputBaseFilename `
    -RetentionRegistry $RetentionRegistry -EphemeralCI:$EphemeralCI
$outputRoot = [IO.Path]::GetFullPath([string]$storageLease.output_root)
$bundleRoot = Join-Path $outputRoot 'dist\E3'
$installerRoot = Join-Path $outputRoot 'installer-dist'
$seedRoot = Join-Path $outputRoot 'packaging\machine-seed'
Write-Host "Build output directory: $outputRoot"
if ($RequireFreshOutputs) {
    foreach ($relative in @('build\E3', 'cache', 'temp', 'dist\E3', 'installer-dist', 'packaging\machine-seed', 'build-info.json', 'E3.spec')) {
        Assert-FreshBuildPath -Path (Join-Path $outputRoot $relative)
    }
}
if ([string]::IsNullOrWhiteSpace($Revision)) {
    $Revision = (git rev-parse HEAD).Trim()
}
if ([string]::IsNullOrWhiteSpace($Revision)) {
    throw "Could not determine the E3 build revision"
}

Confirm-E3BuildReplacement -Python $python -Lease $storageLease
if ($RequireFreshOutputs) {
    foreach ($relative in @('build\E3', 'cache', 'temp', 'dist\E3', 'installer-dist', 'packaging\machine-seed', 'build-info.json', 'E3.spec')) {
        Assert-FreshBuildPath -Path (Join-Path $outputRoot $relative)
    }
} else {
    foreach ($relative in @('build\E3', 'cache', 'temp', 'dist\E3', "installer-dist\$OutputBaseFilename.exe", 'packaging\machine-seed', 'build-info.json', 'E3.spec')) {
        $target = [IO.Path]::GetFullPath((Join-Path $outputRoot $relative))
        if (Test-Path -LiteralPath $target) {
            Remove-Item -LiteralPath $target -Recurse -Force -ErrorAction Stop
        }
    }
}
$savedEnvironment = Enter-E3ManagedBuildEnvironment -OutputRoot $outputRoot
try {
    $env:PATH = $sanitizedPath.Trim()
    & $python -c "import PyInstaller"
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller is required in $python. Prepare the build environment explicitly before retrying; this build does not install or upgrade dependencies."
    }

    & $python -c "from PySide6 import QtCore, QtGui, QtWidgets"
    if ($LASTEXITCODE -ne 0) {
        throw "PySide6 must already be available in $python and import under the sanitized PATH. Prepare the desktop dependencies explicitly before retrying."
    }

    $version = (
        & $python .\packaging\version_for_build.py
    ).Trim()
    if ([string]::IsNullOrWhiteSpace($version)) {
        throw "Could not determine the E3 application version"
    }
    $buildInfo = Join-Path $outputRoot "build-info.json"
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
            Assert-FreshBuildPath -Path (Join-Path $outputRoot $relative)
        }
    }

    & $python -m PyInstaller `
        --noconfirm `
        --clean `
        --windowed `
        --onedir `
        --name E3 `
        --paths $sourceRoot `
        --distpath (Join-Path $outputRoot 'dist') `
        --workpath (Join-Path $outputRoot 'build') `
        --specpath $outputRoot `
        --collect-all laser_aligner `
        --collect-all cv2 `
        --collect-all PySide6 `
        .\packaging\e3_entry.py
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller build failed"
    }

    & $python .\packaging\windows_build_guard.py `
        validate-bundle `
        --internal-root (Join-Path $bundleRoot '_internal')
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller collected conflicting foreign runtime DLLs"
    }
if ($RequireFreshOutputs) {
    $bundleInfo = Join-Path $bundleRoot "build-info.json"
    $bundleConfig = Join-Path $bundleRoot "config"
    Assert-FreshBuildPath -Path $bundleInfo
    [IO.File]::Copy($buildInfo, $bundleInfo, $false)
    Assert-FreshBuildPath -Path $bundleConfig
    New-Item -Path $bundleConfig -ItemType Directory -ErrorAction Stop | Out-Null
    $defaultConfig = Join-Path $bundleConfig "default.json"
    Assert-FreshBuildPath -Path $defaultConfig
    [IO.File]::Copy((Join-Path $sourceRoot "config\default.json"), $defaultConfig, $false)
} else {
    Copy-Item -LiteralPath $buildInfo -Destination (Join-Path $bundleRoot 'build-info.json') -Force
    $bundleConfig = Join-Path $bundleRoot 'config'
    New-Item -Path $bundleConfig -ItemType Directory -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $sourceRoot 'config\default.json') -Destination (Join-Path $bundleConfig 'default.json') -Force
}

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

$defines = @(
    "/DMyAppVersion=$version",
    "/DOutputBaseFilename=$OutputBaseFilename",
    "/DE3BundleRoot=$bundleRoot",
    "/DE3SeedRoot=$seedRoot",
    "/DE3InstallerOutputDir=$installerRoot"
)
if ($MachineSeed) {
    $defines += "/DMachineSeed=1"
}
if ($RequireFreshOutputs) {
    Assert-FreshBuildPath -Path $installerRoot
}
& $iscc @defines .\packaging\E3-installer.iss
if ($LASTEXITCODE -ne 0) {
    throw "Inno Setup compilation failed"
}
Complete-E3BuildStorage -Python $python -Lease $storageLease

Write-Host ""
Write-Host "E3 Windows installer complete:"
$installerPath = (Resolve-Path -LiteralPath (Join-Path $installerRoot "$OutputBaseFilename.exe")).Path
Write-Host $installerPath
if ($PassThru) {
    [pscustomobject]@{
        Kind = 'E3WindowsBuild'
        SourceRoot = $sourceRoot
        OutputRoot = $outputRoot
        Executable = Join-Path $bundleRoot 'E3.exe'
        Installer = $installerPath
        Version = $version
    }
}
} finally {
    Exit-E3ManagedBuildEnvironment -Saved $savedEnvironment
}
