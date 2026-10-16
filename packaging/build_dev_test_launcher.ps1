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
Assert-E3BuildDependencies -Python $Python -Modules @('PyInstaller')
$storageLease = Start-E3BuildStorage -Python $Python -Worktree $repoRoot `
    -Mode launcher -EphemeralCI:$EphemeralCI
$outputRoot = [IO.Path]::GetFullPath([string]$storageLease.output_root)
Write-Host "Launcher output directory: $outputRoot"

$icon = Join-Path $repoRoot "packaging\e3-dev-test.ico"
if (-not (Test-Path $icon)) {
    throw "The E3 DEV TEST icon is missing: $icon"
}
$versionFile = Join-Path $repoRoot "packaging\dev_test_launcher_version.txt"

$workPath = Join-Path $outputRoot "build\dev-test-launcher"
$specPath = Join-Path $outputRoot "build\dev-test-launcher-spec"
$cachePath = Join-Path $outputRoot "cache"
$tempPath = Join-Path $outputRoot "temp"
$distPath = Join-Path $outputRoot "launcher-dist"
Confirm-E3BuildReplacement -Python $Python -Lease $storageLease
foreach ($target in @($workPath, $specPath, $cachePath, $tempPath, (Join-Path $distPath "E3 DEV TEST.exe"))) {
    if (Test-Path -LiteralPath $target) {
        Remove-Item -LiteralPath $target -Recurse -Force -ErrorAction Stop
    }
}

$savedEnvironment = Enter-E3ManagedBuildEnvironment -OutputRoot $outputRoot
try {
    & $Python -c "import PyInstaller"
    if ($LASTEXITCODE -ne 0) {
        throw "The existing PyInstaller installation cannot import under the managed build environment."
    }
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
Complete-E3BuildStorage -Python $Python -Lease $storageLease

Write-Host (Resolve-Path (Join-Path $distPath "E3 DEV TEST.exe"))
} finally {
    Exit-E3ManagedBuildEnvironment -Saved $savedEnvironment
}
