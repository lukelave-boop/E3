<#
.SYNOPSIS
Inventory E3 build artifacts, or explicitly build into fresh output locations.
.DESCRIPTION
No deletion or pruning is implemented here. -Build invokes the mandatory guarded
build_windows.ps1 after a fail-closed inventory and fresh-output checks.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$RegistryPath,
    [string]$LauncherPointer = (Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'E3 Dev Test\current-feature.json'),
    [switch]$Build,
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
$reservationBytes = [long][Math]::Ceiling($BuildReservationGiB * 1GB)
$budgetBytes = [long][Math]::Floor($BudgetGiB * 1GB)
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
    $result = & $Python $retentionTool @Arguments
    $commandExitCode = $LASTEXITCODE
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

function Assert-PlainAncestors {
    param([string]$Path)
    $current = [IO.Path]::GetFullPath($Path)
    while (-not [string]::IsNullOrEmpty($current)) {
        try {
            $item = Get-Item -Force -LiteralPath $current -ErrorAction Stop
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "Refusing a reparse point or junction in the build path: $current"
            }
        } catch [System.Management.Automation.ItemNotFoundException] {
            # A new output can be absent; its existing ancestors still need checking.
        }
        $parent = [IO.Directory]::GetParent($current)
        if ($null -eq $parent) { break }
        $current = $parent.FullName
    }
}

function Assert-FreshOutputs {
    Assert-PlainAncestors -Path $repoRoot
    foreach ($relative in @('build\E3', 'dist\E3', 'installer-dist', 'packaging\machine-seed', 'build-info.json', 'E3.spec')) {
        $target = [IO.Path]::GetFullPath((Join-Path $repoRoot $relative))
        Assert-PlainAncestors -Path $target
        try {
            $null = Get-Item -Force -LiteralPath $target -ErrorAction Stop
        } catch [System.Management.Automation.ItemNotFoundException] {
            continue
        }
        throw "Refusing to replace existing output: $target. Use a fresh lightweight checkout; review old outputs separately."
    }
}

$planArguments = @('plan', '--registry', $RegistryPath, '--launcher-pointer', $LauncherPointer)
$plan = Invoke-RetentionJson -Arguments $planArguments
if (-not $Build) {
    $plan | ConvertTo-Json -Depth 100
    if (-not $plan.inspection_complete) { exit 2 }
    return
}

if (-not $plan.inspection_complete) {
    throw "Build preflight could not establish process, launcher, and registry safety. Inspect a dry-run inventory before continuing."
}
if (([decimal]$plan.registered_logical_bytes + [decimal]$plan.reserved_logical_bytes + $reservationBytes) -gt $budgetBytes) {
    throw 'The registered artifacts and build reservations exceed the admission budget. Review an exact cleanup plan or explicitly choose a reviewed BudgetGiB; nothing was deleted.'
}
Assert-FreshOutputs
if ([string]::IsNullOrWhiteSpace($BuildId)) {
    $BuildId = 'local-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ') + '-' + [Guid]::NewGuid().ToString('N').Substring(0, 8)
}

$originalLocation = Get-Location
$buildSucceeded = $false
try {
    # The canonical builder owns the one mandatory shared storage lease.
    # Creating a second legacy lease here would protect this same worktree
    # against its own canonical admission check.
    Assert-FreshOutputs
    $freshPlan = Invoke-RetentionJson -Arguments $planArguments
    if (-not $freshPlan.inspection_complete) {
        throw 'Build preflight changed; no build was started.'
    }
    $buildParameters = @{
        Channel = $Channel
        OutputBaseFilename = $OutputBaseFilename
        RequireFreshOutputs = $true
        RetentionRegistry = $RegistryPath
    }
    if (-not [string]::IsNullOrWhiteSpace($Revision)) {
        $buildParameters.Revision = $Revision
    }
    & $builder @buildParameters
    if (-not $?) { throw 'The Windows build did not complete successfully.' }

    $null = Invoke-RetentionJson -Arguments @(
        'register', '--registry', $RegistryPath, '--worktree', $repoRoot,
        '--build-id', $BuildId,
        '--bundle', (Join-Path $repoRoot 'dist\E3'),
        '--installer', (Join-Path $repoRoot "installer-dist\$OutputBaseFilename.exe"),
        '--scratch', (Join-Path $repoRoot 'build\E3')
    )
    $buildSucceeded = $true
} finally {
    Set-Location $originalLocation
}
if ($buildSucceeded) {
    Write-Host "Registered completed build $BuildId. No artifacts were deleted or launchers changed."
    $finalPlan = Invoke-RetentionJson -Arguments $planArguments
    $finalPlan | ConvertTo-Json -Depth 100
}
