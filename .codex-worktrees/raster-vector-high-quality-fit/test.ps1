$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($null -ne $py) {
        $python = $py.Source
    } else {
        $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
        if ($null -eq $pythonCommand) {
            Write-Error "Could not find .venv\Scripts\python.exe, py, or python."
            exit 1
        }
        $python = $pythonCommand.Source
    }
}

$workers = 4
$pytestArgs = @()

foreach ($arg in $args) {
    if ($arg -eq "--serial") {
        $workers = 0
    } else {
        $pytestArgs += $arg
    }
}

$commandArgs = @("-m", "pytest", "-q")
if ($workers -gt 0) {
    $commandArgs += @("-n", "$workers")
}
$commandArgs += $pytestArgs

Write-Host "E3 tests:" $python ($commandArgs -join " ")
& $python @commandArgs
exit $LASTEXITCODE
