# Select an installed compiler before build admission; never install dependencies.
function Get-E3InnoCompiler {
    param([string]$ExplicitPath = '')
    if (-not [string]::IsNullOrWhiteSpace($ExplicitPath)) {
        $candidates = @([IO.Path]::GetFullPath($ExplicitPath))
    } else {
        $candidates = @(
            "$env:ProgramFiles\Inno Setup 7\ISCC.exe",
            "${env:ProgramFiles(x86)}\Inno Setup 7\ISCC.exe",
            "$env:LOCALAPPDATA\Programs\Inno Setup 7\ISCC.exe"
        )
    }
    foreach ($candidate in $candidates) {
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { continue }
        $compiler = Get-Item -LiteralPath $candidate -ErrorAction Stop
        # Inno 6 can both fail to open long file names and silently omit deep
        # directories. A successful compilation with it does not prove completeness.
        $info = $compiler.VersionInfo
        if ($info.ProductName -ne 'Inno Setup' -or
            $info.FileDescription -ne 'Inno Setup Command-Line Compiler') { continue }
        # Official ISCC releases have zeroed PE numeric versions. Query the
        # identified compiler itself; /? is read-only in both Inno 6 and 7.
        $previousErrorPreference = $ErrorActionPreference
        try {
            # Windows PowerShell 5.1 turns redirected native help text on stderr
            # into ErrorRecords. Inspect the exit code without terminating on it.
            $ErrorActionPreference = 'Continue'
            $banner = (& $compiler.FullName '/?' 2>&1 | Out-String)
            $compilerExitCode = $LASTEXITCODE
        } finally {
            $ErrorActionPreference = $previousErrorPreference
        }
        if ($compilerExitCode -eq 0 -and
            $banner -match '(?m)^Inno Setup (\d+) Command-Line Compiler' -and
            [int]$Matches[1] -ge 7) {
            return $compiler.FullName
        }
    }
    throw 'Inno Setup 7 or newer is required for complete long-path packaging. Install it explicitly, or pass -InnoCompiler (E3_INNO_COMPILER) for an existing portable compiler. No build reservation or output has been created; the builder does not install or upgrade dependencies.'
}

# Inno 7 converts source paths itself, but long OutputDir values still need an
# explicit prefix. Keep this representation inside compiler calls and PE reads.
function ConvertTo-E3InnoPath {
    param([string]$Path)
    $absolute = [IO.Path]::GetFullPath($Path)
    if ($absolute.StartsWith('\\?\')) { return $absolute }
    if ($absolute.StartsWith('\\')) { return '\\?\UNC\' + $absolute.Substring(2) }
    return '\\?\' + $absolute
}

function ConvertFrom-E3InnoProductVersion {
    param([AllowEmptyString()][string]$ProductVersion)
    # Inno's fixed-width ProductVersion resource retains trailing ASCII spaces.
    # Do not remove leading spaces or other whitespace: identity checks reject it.
    # https://github.com/jrsoftware/issrc/blob/is-7_1_0/Projects/Res/SetupLdr.version.rc
    # https://github.com/jrsoftware/issrc/blob/is-7_1_0/Projects/Src/Compiler.ExeUpdateFunc.pas
    return $ProductVersion.TrimEnd([char]0x20)
}

function Get-E3InstallerProductVersion {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$InstallerPath)

    # Resolve relative paths in PowerShell's location, then support long PE paths.
    $installer = Get-Item -LiteralPath $InstallerPath -ErrorAction Stop
    $readPath = ConvertTo-E3InnoPath -Path $installer.FullName
    $raw = [string][Diagnostics.FileVersionInfo]::GetVersionInfo($readPath).ProductVersion
    $rawJson = ConvertTo-Json -InputObject $raw -Compress
    $codepoints = @($raw.ToCharArray() | ForEach-Object { [int]$_ })
    Write-Verbose ('Installer ProductVersion raw={0} length={1} codepoints=[{2}]' -f
        $rawJson, $raw.Length, ($codepoints -join ','))
    return ConvertFrom-E3InnoProductVersion -ProductVersion $raw
}
