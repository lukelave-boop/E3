<#
.SYNOPSIS
Inventory E3 build artifacts, or explicitly build into fresh output locations.
.DESCRIPTION
Default mode explicitly inventories the legacy cleanup registry. -Build invokes
the mandatory managed-output builder without enumerating or adopting legacy outputs.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$RegistryPath,
    [string]$LauncherPointer = (Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'E3 Dev Test\current-feature.json'),
    [switch]$Build,
    # Compatibility parameter; the mandatory guard assigns the actual storage ID.
    [string]$BuildId = '',
    [string]$Python = '',
    [string]$Revision = '',
    [ValidateScript({ $_ -gt 0 -and $_ -le 1048576 -and -not [double]::IsNaN($_) -and -not [double]::IsInfinity($_) })]
    [double]$BuildReservationGiB = 2,
    [ValidateScript({ $_ -gt 0 -and $_ -le 1048576 -and -not [double]::IsNaN($_) -and -not [double]::IsInfinity($_) })]
    [double]$BudgetGiB = 8,
    [ValidateSet('development')]
    [string]$Channel = 'development',
    [ValidatePattern('^E3(?:-[A-Za-z0-9][A-Za-z0-9._-]{0,76})?$')]
    [ValidateScript({ $_ -notmatch 'home' })]
    [string]$OutputBaseFilename = 'E3-Setup'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$RegistryPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($RegistryPath)
$LauncherPointer = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($LauncherPointer)
$retentionTool = Join-Path $PSScriptRoot 'artifact_retention.py'
$builder = Join-Path $PSScriptRoot 'build_windows.ps1'
if ([string]::IsNullOrWhiteSpace($Python)) {
    $localPython = Join-Path $repoRoot '.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $localPython -PathType Leaf) {
        $Python = $localPython
    } else {
        $Python = (Get-Command python -ErrorAction Stop).Source
    }
}

function Invoke-RetentionJson {
    param([string[]]$Arguments)
    $savedBytecode = [Environment]::GetEnvironmentVariable('PYTHONDONTWRITEBYTECODE', 'Process')
    try {
        $env:PYTHONDONTWRITEBYTECODE = '1'
        $result = & $Python $retentionTool @Arguments
        $commandExitCode = $LASTEXITCODE
    } finally {
        if ($null -eq $savedBytecode) {
            Remove-Item Env:PYTHONDONTWRITEBYTECODE -ErrorAction SilentlyContinue
        } else {
            [Environment]::SetEnvironmentVariable('PYTHONDONTWRITEBYTECODE', $savedBytecode, 'Process')
        }
    }
    $parsed = (($result -join "`n") | ConvertFrom-Json)
    # The core returns a useful JSON inventory with exit 2 for incomplete checks.
    if ($commandExitCode -ne 0 -and -not (
        $Arguments[0] -eq 'plan' -and $commandExitCode -eq 2 -and
        $null -ne $parsed -and $parsed.inspection_complete -eq $false
    )) {
        throw "Artifact retention command failed: $($Arguments[0])"
    }
    return $parsed
}

if (-not $Build) {
    $plan = Invoke-RetentionJson -Arguments @('plan', '--registry', $RegistryPath, '--launcher-pointer', $LauncherPointer)
    $plan | ConvertTo-Json -Depth 100
    if (-not $plan.inspection_complete) { exit 2 }
    return
}
if ($BuildReservationGiB -ne 2 -or $BudgetGiB -ne 8) {
    throw 'Managed builds use the fixed shared 8 GiB cap and 2 GiB reservation. Legacy registry budget parameters cannot change that policy.'
}
$originalLocation = Get-Location
$buildSucceeded = $false
try {
    # The canonical builder alone owns admission and the managed output lease.
    $buildParameters = @{
        Channel = $Channel
        OutputBaseFilename = $OutputBaseFilename
        RetentionRegistry = $RegistryPath
        PassThru = $true
    }
    if (-not [string]::IsNullOrWhiteSpace($Revision)) {
        $buildParameters.Revision = $Revision
    }
    $buildResult = $null
    & $builder @buildParameters | ForEach-Object {
        if ($_.PSObject.Properties.Name -contains 'Kind' -and $_.Kind -eq 'E3WindowsBuild') {
            $buildResult = $_
        } else {
            Write-Host $_
        }
    }
    if (-not $?) { throw 'The Windows build did not complete successfully.' }
    if ($null -eq $buildResult) { throw 'The guarded builder did not return its actual output paths.' }

    $actualOutput = [IO.Path]::GetFullPath([string]$buildResult.OutputRoot)
    if ([IO.Directory]::GetParent($actualOutput).FullName -ne (Join-Path $repoRoot '.e3-builds')) {
        throw 'The normal builder did not return a managed output directory.'
    }
    Write-Host "Managed output is recorded by the shared storage guard: $actualOutput"
    Write-Host 'Legacy cleanup registration is unchanged; this managed build was not registered under source-checkout paths.'
    $buildSucceeded = $true
} finally {
    Set-Location $originalLocation
}
if ($buildSucceeded) {
    Write-Host "Completed managed build at $($buildResult.OutputRoot). No launcher was changed."
}
