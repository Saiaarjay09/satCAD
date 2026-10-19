; Inno Setup recipe: turns the PyInstaller output (dist\satCAD) into a Windows installer.
; Build:  iscc /DAppVersion=0.4.0 satcad_app.iss      -> installer\satCAD-0.4.0-windows-x64-setup.exe
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
[Setup]
AppName=satCAD
AppVersion={#AppVersion}
AppPublisher=satCAD contributors
DefaultDirName={autopf}\satCAD
DefaultGroupName=satCAD
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=installer
OutputBaseFilename=satCAD-{#AppVersion}-windows-x64-setup
Compression=lzma2/fast
SolidCompression=yes
UninstallDisplayName=satCAD
DisableProgramGroupPage=yes
[Files]
Source: "dist\satCAD\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
[Icons]
Name: "{autoprograms}\satCAD"; Filename: "{app}\satCAD.exe"
Name: "{autodesktop}\satCAD"; Filename: "{app}\satCAD.exe"; Tasks: desktopicon
[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked
[Run]
Filename: "{app}\satCAD.exe"; Description: "Start satCAD"; Flags: nowait postinstall skipifsilent
