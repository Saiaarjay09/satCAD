; Inno Setup recipe: turns the PyInstaller output (dist\SatCAD Studio) into a Windows installer.
; Build:  iscc /DAppVersion=0.3.0 studio.iss      -> installer\SatCAD-Studio-0.3.0-windows-x64-setup.exe
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
[Setup]
AppName=SatCAD Studio
AppVersion={#AppVersion}
AppPublisher=SatCAD contributors
DefaultDirName={autopf}\SatCAD Studio
DefaultGroupName=SatCAD Studio
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=installer
OutputBaseFilename=SatCAD-Studio-{#AppVersion}-windows-x64-setup
Compression=lzma2/fast
SolidCompression=yes
UninstallDisplayName=SatCAD Studio
DisableProgramGroupPage=yes
[Files]
Source: "dist\SatCAD Studio\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
[Icons]
Name: "{autoprograms}\SatCAD Studio"; Filename: "{app}\SatCAD Studio.exe"
Name: "{autodesktop}\SatCAD Studio"; Filename: "{app}\SatCAD Studio.exe"; Tasks: desktopicon
[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked
[Run]
Filename: "{app}\SatCAD Studio.exe"; Description: "Start SatCAD Studio"; Flags: nowait postinstall skipifsilent
