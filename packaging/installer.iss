; Inno Setup script for the Windows installer. Built by packaging/build_windows.ps1, which
; passes the version: ISCC /DAppVersion=<v> packaging\installer.iss
;
; Per-user install (no admin prompt): the app, its settings and its backups belong to the
; user who plays. The identity (AppId) must never change, or Windows lists a second program
; and an upgrade leaves the old one behind.

#define AppName "Ailurus Tone Editor"
#define AppExe "Ailurus Tone Editor.exe"
#ifndef AppVersion
  #error Pass the version: ISCC /DAppVersion=<version> packaging\installer.iss
#endif

[Setup]
SourceDir=..
AppId={{9AAD3BF5-4E1F-428E-B2FD-3BE8400C7240}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=David Urban
AppPublisherURL=https://github.com/TursiThePanda/ailurus-tone-editor
AppSupportURL=https://github.com/TursiThePanda/ailurus-tone-editor/issues
AppUpdatesURL=https://github.com/TursiThePanda/ailurus-tone-editor/releases
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\{#AppExe}
SetupIconFile=assets\build\icon.ico
LicenseFile=LICENSE
OutputDir=dist
OutputBaseFilename=Ailurus-Tone-Editor-{#AppVersion}-windows-setup
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
; the running app holds the unit's MIDI port and our port 8710: close it before replacing files
CloseApplications=yes
RestartApplications=no

[Files]
Source: "dist\{#AppName}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion

; an upgrade must not keep files a newer build no longer has (old library versions)
[InstallDelete]
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: postinstall nowait skipifsilent
