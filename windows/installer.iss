; Inno Setup script: turns the built app folder into one normal installer (NYXOR-Setup.exe).
; Built by build_windows.ps1 (needs Inno Setup 6). Per-user install: no administrator prompt.
#ifndef SourceDir
  #define SourceDir "..\dist\msix\dist\NYXOR"
#endif
#ifndef AppVersion
  #define AppVersion "2.3.0"
#endif
[Setup]
AppId={{6C1F7A52-3B0E-4E0B-9A57-4E59584F5231}
AppName=NYXOR
AppVersion={#AppVersion}
AppPublisher={#GetEnv("NYXOR_PUBLISHER")}
DefaultDirName={autopf}\NYXOR
DefaultGroupName=NYXOR
UninstallDisplayIcon={app}\NYXOR.exe
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
OutputDir=..\dist
OutputBaseFilename=NYXOR-Setup
[Tasks]
Name: "desktopicon"; Description: "Create a desktop icon"; GroupDescription: "Shortcuts:"
[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{group}\NYXOR"; Filename: "{app}\NYXOR.exe"
Name: "{autodesktop}\NYXOR"; Filename: "{app}\NYXOR.exe"; Tasks: desktopicon
[Run]
Filename: "{app}\NYXOR.exe"; Description: "Start NYXOR"; Flags: nowait postinstall skipifsilent
