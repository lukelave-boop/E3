#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif
#ifndef OutputBaseFilename
  #define OutputBaseFilename "E3-Setup"
#endif

#define MyAppName "E3 Positioning System"
#define MyAppExeName "E3.exe"

[Setup]
AppId={{86D8F6CB-648E-4D56-99E8-72F1B3F0D2A1}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=E3
DefaultDirName={localappdata}\Programs\E3 Positioning System
DefaultGroupName=E3 Positioning System
DisableProgramGroupPage=yes
OutputDir=..\installer-dist
OutputBaseFilename={#OutputBaseFilename}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#MyAppExeName}
CloseApplications=yes
RestartApplications=yes
UsePreviousAppDir=yes
SetupLogging=yes

[Files]
Source: "..\dist\E3\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
#ifdef MachineSeed
Source: "machine-seed\config\network-local.json"; DestDir: "{localappdata}\E3 Positioning System\config"; Flags: ignoreversion onlyifdoesntexist uninsneveruninstall
Source: "machine-seed\data\calibration_profiles\*"; DestDir: "{localappdata}\E3 Positioning System\data\calibration_profiles"; Flags: ignoreversion recursesubdirs createallsubdirs onlyifdoesntexist uninsneveruninstall
Source: "machine-seed\data\machines.json"; DestDir: "{localappdata}\E3 Positioning System\data"; Flags: ignoreversion onlyifdoesntexist uninsneveruninstall
Source: "machine-seed\secrets\bridge-token.txt"; DestDir: "{localappdata}\E3 Positioning System\secrets"; Flags: ignoreversion onlyifdoesntexist uninsneveruninstall
#endif

[Icons]
Name: "{autodesktop}\E3 Positioning System"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{group}\E3 Positioning System"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch E3 Positioning System"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent
