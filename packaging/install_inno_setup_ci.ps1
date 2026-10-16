param(
    [string]$Destination = '',
    [switch]$AllowLocal
)

$ErrorActionPreference = 'Stop'
# Explicit dependency preparation only; the canonical builder never calls this.
# Official immutable release and publisher verification:
# https://jrsoftware.org/isdl.php
# https://jrsoftware.org/isdl-verify.php
# Portable behavior: https://github.com/jrsoftware/issrc/blob/is-7_1_0/isportable.iss
$version = '7.1.0'
$expectedHash = '0362a383ed217d4c4239b5933866dd96d3eb2102737da92f80f6057a4b40df2f'
$uri = 'https://github.com/jrsoftware/issrc/releases/download/is-7_1_0/innosetup-7.1.0-x64.exe'
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    throw 'Inno Setup dependency preparation requires Windows.'
}
$hosted = $env:GITHUB_ACTIONS -eq 'true' -and $env:RUNNER_ENVIRONMENT -eq 'github-hosted' -and
    -not [string]::IsNullOrWhiteSpace($env:GITHUB_WORKSPACE) -and
    [IO.Path]::GetFullPath($env:GITHUB_WORKSPACE) -eq $repoRoot -and
    -not [string]::IsNullOrWhiteSpace($env:RUNNER_TEMP)
if (-not $hosted -and (-not $AllowLocal -or [string]::IsNullOrWhiteSpace($Destination))) {
    throw 'Outside the exact hosted Actions workspace, explicitly supply -AllowLocal and a new -Destination.'
}
if ([string]::IsNullOrWhiteSpace($Destination)) {
    $Destination = Join-Path $env:RUNNER_TEMP "e3-inno-$version"
}
if ($Destination -notmatch '^(?:[A-Za-z]:[\\/]|\\\\[^\\/]+[\\/][^\\/]+[\\/])') {
    throw 'The dependency destination must be an absolute path.'
}
$Destination = [IO.Path]::GetFullPath($Destination)
if (Test-Path -LiteralPath $Destination) {
    throw "Dependency destination already exists; it will not be replaced: $Destination"
}
$current = [IO.Directory]::GetParent($Destination).FullName
while (-not [string]::IsNullOrWhiteSpace($current)) {
    $item = Get-Item -Force -LiteralPath $current -ErrorAction Stop
    if (-not $item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Dependency destination has a non-directory or reparse ancestor: $current"
    }
    $parent = [IO.Directory]::GetParent($current)
    if ($null -eq $parent) { break }
    $current = $parent.FullName
}

function Assert-PinnedInnoBinary {
    param([string]$Path)
    $signature = Get-AuthenticodeSignature -LiteralPath $Path
    if ($signature.Status -ne 'Valid' -or $null -eq $signature.SignerCertificate -or
        $signature.SignerCertificate.GetNameInfo(
            [Security.Cryptography.X509Certificates.X509NameType]::SimpleName, $false
        ) -ne 'Pyrsys B.V.') {
        throw "Inno Setup binary does not have a valid Pyrsys B.V. signature: $Path"
    }
}

New-Item -ItemType Directory -Path $Destination -ErrorAction Stop | Out-Null
$installer = Join-Path $Destination "innosetup-$version-x64.exe"
$compilerRoot = Join-Path $Destination 'compiler'
$installLog = Join-Path $Destination 'dependency-install.log'
Invoke-WebRequest -Uri $uri -OutFile $installer
if ((Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash -ne $expectedHash) {
    throw 'Downloaded Inno Setup installer does not match the pinned official SHA-256.'
}
# The signed setup loader has no file-version resource; its pinned hash identifies the release.
Assert-PinnedInnoBinary -Path $installer
$arguments = @(
    '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-', '/PORTABLE=1',
    '/CURRENTUSER', '/NOICONS', ('/DIR="{0}"' -f $compilerRoot), ('/LOG="{0}"' -f $installLog)
)
$process = Start-Process -FilePath $installer -ArgumentList $arguments -WindowStyle Hidden -Wait -PassThru
if ($process.ExitCode -ne 0) {
    throw "Inno Setup dependency preparation failed with exit code $($process.ExitCode); see $installLog"
}
$compiler = Join-Path $compilerRoot 'ISCC.exe'
Assert-PinnedInnoBinary -Path $compiler
# Inno's PE version resource is zero; ask the already trusted compiler for its engine version.
$versionOutput = Join-Path $Destination 'compiler-version.txt'
$versionError = Join-Path $Destination 'compiler-version-error.txt'
$versionProcess = Start-Process -FilePath $compiler -ArgumentList '--version' `
    -WindowStyle Hidden -RedirectStandardOutput $versionOutput -RedirectStandardError $versionError -PassThru
try {
    if (-not $versionProcess.WaitForExit(10000)) {
        $versionProcess.Kill()
        throw 'Inno Setup compiler version check exceeded ten seconds.'
    }
    $compilerVersion = (Get-Content -LiteralPath $versionOutput -Raw).Trim()
    if ($versionProcess.ExitCode -ne 0 -or $compilerVersion -ne $version) {
        throw "Expected Inno Setup $version; found $compilerVersion"
    }
} finally {
    $versionProcess.Dispose()
}
$env:E3_INNO_COMPILER = $compiler
if ($hosted -and -not [string]::IsNullOrWhiteSpace($env:GITHUB_ENV)) {
    "E3_INNO_COMPILER=$compiler" | Out-File -FilePath $env:GITHUB_ENV -Append -Encoding utf8
}
Write-Host "Verified portable Inno Setup $version compiler: $compiler"
$compiler
