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
    if ($EphemeralCI) { $arguments += '--ephemeral-ci' }
    if (-not [string]::IsNullOrWhiteSpace($RetentionRegistry)) {
        $arguments += @('--retention-registry', $RetentionRegistry)
    }
    return (Invoke-E3StorageJson -Python $Python -Arguments $arguments)
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
