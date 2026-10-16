# Shared command adapter for the mandatory normal-entry build storage guard.
# Never pass machine configuration, calibration content, or credentials here.
function Restore-E3EnvironmentValue {
    param([string]$Name, [object]$Value)
    if ($null -eq $Value) {
        Remove-Item -LiteralPath ('Env:' + $Name) -ErrorAction SilentlyContinue
    } else {
        [Environment]::SetEnvironmentVariable($Name, [string]$Value, 'Process')
    }
}

function Assert-E3BuildDependencies {
    param([string]$Python, [string[]]$Modules)
    $savedBytecode = [Environment]::GetEnvironmentVariable('PYTHONDONTWRITEBYTECODE', 'Process')
    $savedUserSite = [Environment]::GetEnvironmentVariable('PYTHONNOUSERSITE', 'Process')
    try {
        $env:PYTHONDONTWRITEBYTECODE = '1'
        $env:PYTHONNOUSERSITE = '1'
        & $Python -c "import importlib.util,sys; missing=[name for name in sys.argv[1:] if importlib.util.find_spec(name) is None]; sys.exit('Missing build dependencies: '+', '.join(missing) if missing else 0)" @Modules
        if ($LASTEXITCODE -ne 0) {
            throw "Required build dependencies are missing from $Python. Prepare PyInstaller and the E3 desktop dependencies explicitly before retrying; this build does not install or upgrade packages."
        }
    } finally {
        Restore-E3EnvironmentValue -Name 'PYTHONDONTWRITEBYTECODE' -Value $savedBytecode
        Restore-E3EnvironmentValue -Name 'PYTHONNOUSERSITE' -Value $savedUserSite
    }
}

function Invoke-E3StorageJson {
    param([string]$Python, [string[]]$Arguments)
    $tool = Join-Path $PSScriptRoot 'build_storage_guard.py'
    $originalBytecode = [Environment]::GetEnvironmentVariable('PYTHONDONTWRITEBYTECODE', 'Process')
    try {
        $env:PYTHONDONTWRITEBYTECODE = '1'
        $output = & $Python $tool @Arguments
        if ($LASTEXITCODE -ne 0) {
            throw "E3 build storage guard refused $($Arguments[0]). Review the reported protected paths or storage limit; no automatic cleanup is available."
        }
        return (($output -join "`n") | ConvertFrom-Json)
    } finally {
        Restore-E3EnvironmentValue -Name 'PYTHONDONTWRITEBYTECODE' -Value $originalBytecode
    }
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
        ) -or (-not $EphemeralCI -and $outputRoot -eq $sourceRoot)) {
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

function Enter-E3ManagedBuildEnvironment {
    param([string]$OutputRoot)
    # Called only after the guard verifies replacement and exact old outputs are removed.
    $cache = Join-Path $OutputRoot 'cache'
    $temporary = Join-Path $OutputRoot 'temp'
    New-Item -Path $cache -ItemType Directory -ErrorAction Stop | Out-Null
    New-Item -Path $temporary -ItemType Directory -ErrorAction Stop | Out-Null
    $values = @{
        TEMP = $temporary
        TMP = $temporary
        TMPDIR = $temporary
        PIP_CACHE_DIR = Join-Path $cache 'pip'
        PYINSTALLER_CONFIG_DIR = Join-Path $cache 'pyinstaller'
        PYTHONPYCACHEPREFIX = Join-Path $cache 'pycache'
        PYTHONDONTWRITEBYTECODE = '1'
        PYTHONUSERBASE = Join-Path $cache 'python-user'
        PYTHONNOUSERSITE = '1'
    }
    $saved = @{}
    foreach ($name in $values.Keys) {
        $saved[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
    }
    $saved['PATH'] = [Environment]::GetEnvironmentVariable('PATH', 'Process')
    try {
        foreach ($name in $values.Keys) {
            [Environment]::SetEnvironmentVariable($name, $values[$name], 'Process')
        }
    } catch {
        Exit-E3ManagedBuildEnvironment -Saved $saved
        throw
    }
    return $saved
}

function Exit-E3ManagedBuildEnvironment {
    param([hashtable]$Saved)
    foreach ($name in $Saved.Keys) {
        Restore-E3EnvironmentValue -Name $name -Value $Saved[$name]
    }
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
