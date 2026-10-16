# Shared command adapter for the mandatory normal-entry build storage guard.
# Never pass machine configuration, calibration content, or credentials here.
function Invoke-E3StorageJson {
    param([string]$Python, [string[]]$Arguments)
    $tool = Join-Path $PSScriptRoot 'build_storage_guard.py'
    $output = & $Python $tool @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "E3 build storage guard refused $($Arguments[0]). Review the reported protected paths or storage limit; no automatic cleanup is available."
    }
    return (($output -join "`n") | ConvertFrom-Json)
}

function Start-E3BuildStorage {
    param(
        [string]$Python,
        [string]$Worktree,
        [ValidateSet('windows', 'private', 'launcher', 'linux')]
        [string]$Mode,
        [string]$InstallerName = 'E3-Setup',
        [string]$RetentionRegistry = '',
        [switch]$EphemeralCI
    )
    $buildId = $Mode + '-' + [Guid]::NewGuid().ToString('N')
    $arguments = @(
        'begin', '--worktree', $Worktree, '--mode', $Mode,
        '--build-id', $buildId, '--pid', [string]$PID,
        '--installer-name', $InstallerName
    )
    if ($Mode -in @('windows', 'private', 'launcher')) { $arguments += '--managed-outputs' }
    if ($EphemeralCI) { $arguments += '--ephemeral-ci' }
    if (-not [string]::IsNullOrWhiteSpace($RetentionRegistry)) {
        $arguments += @('--retention-registry', $RetentionRegistry)
    }
    $lease = Invoke-E3StorageJson -Python $Python -Arguments $arguments
    if ($Mode -in @('windows', 'private', 'launcher')) {
        $sourceRoot = [IO.Path]::GetFullPath($Worktree)
        if (-not [IO.Path]::IsPathRooted([string]$lease.source_root) -or
            -not [IO.Path]::IsPathRooted([string]$lease.output_root)) {
            throw 'Build storage guard did not return absolute source and output roots.'
        }
        $returnedSource = [IO.Path]::GetFullPath([string]$lease.source_root)
        $outputRoot = [IO.Path]::GetFullPath([string]$lease.output_root)
        $managedParent = Join-Path $sourceRoot '.e3-builds'
        if ($returnedSource -ne $sourceRoot -or (
            $outputRoot -ne $sourceRoot -and [IO.Directory]::GetParent($outputRoot).FullName -ne $managedParent
        )) {
            throw 'Build storage guard returned an output outside its source checkout or managed leaf.'
        }
        $outputItem = Get-Item -Force -LiteralPath $outputRoot -ErrorAction Stop
        if (-not $outputItem.PSIsContainer -or
            ($outputItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw 'Build output root is not a plain leased directory.'
        }
    }
    return $lease
}

function Confirm-E3BuildReplacement {
    param([string]$Python, [object]$Lease)
    $null = Invoke-E3StorageJson -Python $Python -Arguments @(
        'verify', '--state', [string]$Lease.state,
        '--build-id', [string]$Lease.build_id, '--token', [string]$Lease.token,
        '--pid', [string]$PID
    )
}

function Complete-E3BuildStorage {
    param([string]$Python, [object]$Lease)
    $null = Invoke-E3StorageJson -Python $Python -Arguments @(
        'complete', '--state', [string]$Lease.state,
        '--build-id', [string]$Lease.build_id, '--token', [string]$Lease.token,
        '--pid', [string]$PID
    )
}
