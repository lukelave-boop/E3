param(
    [string]$Python = ".\.venv\Scripts\python.exe",
    [switch]$EphemeralCI
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$sourceRoot = $repoRoot
Set-Location $repoRoot

if (-not (Test-Path $Python)) {
    $Python = (Get-Command python -ErrorAction Stop).Source
} else {
    $Python = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Python)
}
. (Join-Path $PSScriptRoot "build_storage_gate.ps1")
$storageLease = Start-E3BuildStorage -Python $Python -Worktree $repoRoot `
    -Mode launcher -EphemeralCI:$EphemeralCI
$outputRoot = [IO.Path]::GetFullPath([string]$storageLease.output_root)
if ($outputRoot -ne $sourceRoot) {
    Write-Host "Preserving previous launcher output; building in managed directory: $outputRoot"
}
& $Python -c "import PyInstaller"
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller is required to build E3 DEV TEST"
}

$icon = Join-Path $repoRoot "packaging\e3-dev-test.ico"
if (-not (Test-Path $icon)) {
    throw "The E3 DEV TEST icon is missing: $icon"
}
$versionFile = Join-Path $repoRoot "packaging\dev_test_launcher_version.txt"

$workPath = Join-Path $outputRoot "build\dev-test-launcher"
$specPath = Join-Path $outputRoot "build\dev-test-launcher-spec"
$cachePath = Join-Path $outputRoot "build\pyinstaller-cache"
$distPath = Join-Path $outputRoot "launcher-dist"
Confirm-E3BuildReplacement -Python $Python -Lease $storageLease
foreach ($target in @($workPath, $specPath, $cachePath, (Join-Path $distPath "E3 DEV TEST.exe"))) {
    if (Test-Path -LiteralPath $target) {
        Remove-Item -LiteralPath $target -Recurse -Force -ErrorAction Stop
    }
}

$originalPyInstallerConfig = $env:PYINSTALLER_CONFIG_DIR
New-Item -Path $cachePath -ItemType Directory -ErrorAction Stop | Out-Null
try {
    $env:PYINSTALLER_CONFIG_DIR = $cachePath
    & $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name "E3 DEV TEST" `
    --paths $sourceRoot `
    --icon $icon `
    --version-file $versionFile `
    --distpath $distPath `
    --workpath $workPath `
    --specpath $specPath `
    ".\packaging\e3_dev_test_entry.py"
    if ($LASTEXITCODE -ne 0) {
        throw "E3 DEV TEST PyInstaller build failed"
    }
} finally {
    $env:PYINSTALLER_CONFIG_DIR = $originalPyInstallerConfig
}
Complete-E3BuildStorage -Python $Python -Lease $storageLease

Write-Host (Resolve-Path (Join-Path $distPath "E3 DEV TEST.exe"))
