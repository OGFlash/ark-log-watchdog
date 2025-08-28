#define AppName "ARK Watchdog"
#define AppVersion "1.0.0"
#define Publisher "Andrew King aka Tiny"
#define ExeName "ARK-Watchdog.exe"

[Setup]
AppId={{8E1B4E2E-2C1B-41E8-9C0F-2DB5F7E3A9C1}}
AppName={#AppName}
AppVersion={#AppVersion}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=no
OutputDir=dist\installer
OutputBaseFilename={#AppName}-Setup
Compression=lzma
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
; copy everything produced by PyInstaller
Source: "dist\ARK-Watchdog\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#ExeName}"
Name: "{commondesktop}\{#AppName}"; Filename: "{app}\{#ExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"; Flags: unchecked

[Run]
Filename: "{app}\{#ExeName}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent
