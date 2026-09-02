param(
    [string]$LauncherSource = ".\launcher-dist\E3 DEV TEST.exe",
    [Parameter(Mandatory = $true)]
    [string]$PointerSource,
    [string]$InstallDirectory = "",
    [string]$DesktopDirectory = "",
    [string]$Python = ".\.venv\Scripts\python.exe"
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $repoRoot

$sourceLauncher = (Resolve-Path $LauncherSource).Path
$sourcePointer = (Resolve-Path $PointerSource).Path
if (-not (Test-Path $Python)) {
    $Python = (Get-Command python -ErrorAction Stop).Source
}
& $Python -c "import sys; from pathlib import Path; from laser_aligner.dev_test_launcher import load_feature_pointer; load_feature_pointer(Path(sys.argv[1]))" $sourcePointer
if ($LASTEXITCODE -ne 0) {
    throw "The E3 DEV TEST pointer failed validation"
}
if ([string]::IsNullOrWhiteSpace($InstallDirectory)) {
    $InstallDirectory = Join-Path `
        ([Environment]::GetFolderPath("MyDocuments")) `
        "E3 Dev Test"
}
if ([string]::IsNullOrWhiteSpace($DesktopDirectory)) {
    $DesktopDirectory = [Environment]::GetFolderPath("Desktop")
}

$installRoot = [IO.Path]::GetFullPath($InstallDirectory)
$desktopRoot = [IO.Path]::GetFullPath($DesktopDirectory)
New-Item $installRoot -ItemType Directory -Force | Out-Null
New-Item $desktopRoot -ItemType Directory -Force | Out-Null

$targetLauncher = Join-Path $installRoot "E3 DEV TEST.exe"
$targetPointer = Join-Path $installRoot "current-feature.json"
Copy-Item -LiteralPath $sourceLauncher -Destination $targetLauncher -Force
& $Python -c "import sys; from pathlib import Path; from laser_aligner.dev_test_launcher import load_feature_pointer, write_feature_pointer; write_feature_pointer(Path(sys.argv[2]), load_feature_pointer(Path(sys.argv[1]), launcher_path=Path(sys.argv[3])))" $sourcePointer $targetPointer $targetLauncher
if ($LASTEXITCODE -ne 0) {
    throw "Could not install the validated E3 DEV TEST pointer"
}

$shortcutSource = @"
using System;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;

[ComImport]
[Guid("00021401-0000-0000-C000-000000000046")]
internal class ShellLinkClass { }

[ComImport]
[InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
[Guid("000214F9-0000-0000-C000-000000000046")]
internal interface IShellLinkW
{
    void GetPath(IntPtr pszFile, int cchMaxPath, IntPtr pfd, uint fFlags);
    void GetIDList(out IntPtr ppidl);
    void SetIDList(IntPtr pidl);
    void GetDescription(IntPtr pszName, int cchMaxName);
    void SetDescription([MarshalAs(UnmanagedType.LPWStr)] string pszName);
    void GetWorkingDirectory(IntPtr pszDir, int cchMaxPath);
    void SetWorkingDirectory([MarshalAs(UnmanagedType.LPWStr)] string pszDir);
    void GetArguments(IntPtr pszArgs, int cchMaxPath);
    void SetArguments([MarshalAs(UnmanagedType.LPWStr)] string pszArgs);
    void GetHotkey(out short pwHotkey);
    void SetHotkey(short wHotkey);
    void GetShowCmd(out int piShowCmd);
    void SetShowCmd(int iShowCmd);
    void GetIconLocation(IntPtr pszIconPath, int cchIconPath, out int piIcon);
    void SetIconLocation([MarshalAs(UnmanagedType.LPWStr)] string pszIconPath, int iIcon);
    void SetRelativePath([MarshalAs(UnmanagedType.LPWStr)] string pszPathRel, uint dwReserved);
    void Resolve(IntPtr hwnd, uint fFlags);
    void SetPath([MarshalAs(UnmanagedType.LPWStr)] string pszFile);
}

[StructLayout(LayoutKind.Sequential, Pack = 4)]
internal struct PropertyKey
{
    internal Guid FormatId;
    internal uint PropertyId;

    internal PropertyKey(Guid formatId, uint propertyId)
    {
        FormatId = formatId;
        PropertyId = propertyId;
    }
}

[StructLayout(LayoutKind.Explicit, Size = 16)]
internal struct PropVariant
{
    [FieldOffset(0)] internal ushort VariantType;
    [FieldOffset(8)] internal IntPtr PointerValue;
}

[ComImport]
[InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
[Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99")]
internal interface IPropertyStore
{
    uint GetCount();
    void GetAt(uint propertyIndex, out PropertyKey key);
    void GetValue(ref PropertyKey key, out PropVariant value);
    void SetValue(ref PropertyKey key, ref PropVariant value);
    void Commit();
}

public static class E3DevTestShortcut
{
    public static void Create(
        string shortcutPath,
        string targetPath,
        string workingDirectory,
        string iconPath,
        string appUserModelId)
    {
        object shellObject = new ShellLinkClass();
        try
        {
            IShellLinkW shellLink = (IShellLinkW)shellObject;
            shellLink.SetPath(targetPath);
            shellLink.SetWorkingDirectory(workingDirectory);
            shellLink.SetDescription("Launch the current E3 development feature build");
            shellLink.SetIconLocation(iconPath, 0);
            shellLink.SetShowCmd(1);

            PropertyKey key = new PropertyKey(
                new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"),
                5
            );
            PropVariant value = new PropVariant();
            value.VariantType = 31;
            value.PointerValue = Marshal.StringToCoTaskMemUni(appUserModelId);
            try
            {
                IPropertyStore propertyStore = (IPropertyStore)shellObject;
                propertyStore.SetValue(ref key, ref value);
                propertyStore.Commit();
                ((IPersistFile)shellObject).Save(shortcutPath, true);
            }
            finally
            {
                Marshal.FreeCoTaskMem(value.PointerValue);
            }
        }
        finally
        {
            if (Marshal.IsComObject(shellObject))
            {
                Marshal.FinalReleaseComObject(shellObject);
            }
        }
    }
}
"@

Add-Type -TypeDefinition $shortcutSource -Language CSharp
$shortcutPath = Join-Path $desktopRoot "E3 DEV TEST.lnk"
[E3DevTestShortcut]::Create(
    $shortcutPath,
    $targetLauncher,
    $installRoot,
    $targetLauncher,
    "E3.DevTest"
)

Write-Host "Launcher: $targetLauncher"
Write-Host "Pointer: $targetPointer"
Write-Host "Desktop shortcut: $shortcutPath"
