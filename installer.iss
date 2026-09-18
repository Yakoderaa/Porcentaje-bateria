#define MyAppName "Porcentaje de batería"
#define MyAppExeName "PorcentajeBateria.exe"
#ifndef MyAppVersion
  #define MyAppVersion "0.1.0"
#endif
[Setup]
AppId={{B32A50EF-9B6C-46E8-9A0C-7A76E2F934B4}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
DefaultDirName={autopf}\PorcentajeBateria
DefaultGroupName={#MyAppName}
OutputDir=installer_out
OutputBaseFilename=PorcentajeBateria-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
CloseApplications=force
RestartApplications=no
UninstallDisplayIcon={app}\{#MyAppExeName}
[Files]
Source: "dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Abrir {#MyAppName}"; Flags: nowait runasoriginaluser
