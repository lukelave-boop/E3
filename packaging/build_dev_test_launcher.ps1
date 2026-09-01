param(
    [string]$Python = ".\.venv\Scripts\python.exe"
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $repoRoot

if (-not (Test-Path $Python)) {
    $Python = (Get-Command python -ErrorAction Stop).Source
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

$workPath = Join-Path $repoRoot "build\dev-test-launcher"
$specPath = Join-Path $repoRoot "build\dev-test-launcher-spec"
$distPath = Join-Path $repoRoot "launcher-dist"
Remove-Item $workPath -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item $specPath -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item (Join-Path $distPath "E3 DEV TEST.exe") -Force -ErrorAction SilentlyContinue

& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name "E3 DEV TEST" `
    --icon $icon `
    --version-file $versionFile `
    --distpath $distPath `
    --workpath $workPath `
    --specpath $specPath `
    ".\packaging\e3_dev_test_entry.py"
if ($LASTEXITCODE -ne 0) {
    throw "E3 DEV TEST PyInstaller build failed"
}

Write-Host (Resolve-Path (Join-Path $distPath "E3 DEV TEST.exe"))
