; Inno Setup Script for ARK Watchdog (single EXE)
; Save as installer\ArkWatchdog.iss and compile with Inno Setup 6.x

#define MyAppName        "ARK Watchdog"
#define MyAppVersion     "1.0.0"
#define MyAppPublisher   "Andrew King aka Tiny"
#define MyAppExeName     "ArkWatchdog.exe"

[Setup]
AppId={{E0C0E1B6-37B2-4F4D-9E5A-6B2E7B5A0F5F}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}

; ── Install location (per-user, no admin) ──────────────────────────────────────
; NOTE: Some Inno versions don’t support PrivilegesRequired=lowest.
; If your compiler complains, change the next line to: PrivilegesRequired=admin
DefaultDirName={userappdata}\ArkWatchdog
DefaultGroupName={#MyAppName}
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

; ── Output installer ───────────────────────────────────────────────────────────
OutputDir=.\output
OutputBaseFilename=Setup_{#MyAppName}_{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
DisableDirPage=no
DisableProgramGroupPage=yes
VersionInfoVersion={#MyAppVersion}

; ── Icons (optional) ───────────────────────────────────────────────────────────
; Make sure this file exists; otherwise comment these two lines.
SetupIconFile=.\assets\ark.ico
UninstallDisplayIcon={app}\{#MyAppExeName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"; Flags: unchecked

[Files]
; ── Main EXE from PyInstaller (single-file build placed directly in .\dist) ────
; If you did a one-folder build (dist\ArkWatchdog\ArkWatchdog.exe),
; change Source to: ".\dist\ArkWatchdog\{#MyAppExeName}"
Source: ".\dist\{#MyAppExeName}"; DestDir: "{app}"; DestName: "{#MyAppExeName}"; Flags: ignoreversion

; ── Bundle portable Tesseract next to the app ──────────────────────────────────
; This must contain: Tesseract-OCR\tesseract.exe and Tesseract-OCR\tessdata\eng.traineddata
Source: ".\third_party\Tesseract-OCR\*"; DestDir: "{app}\Tesseract-OCR"; Flags: recursesubdirs ignoreversion

; ── Optional defaults (safe) ───────────────────────────────────────────────────
; Source: ".\config.yaml"; DestDir: "{app}"; Flags: onlyifdoesntexist ignoreversion

[Icons]
; Start Menu shortcut
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
; Desktop shortcut (via task)
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon; WorkingDir: "{app}"

[Run]
; Launch after install
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Clean up optional logs/cache you create (be conservative)
Type: filesandordirs; Name: "{userappdata}\ArkWatchdog\logs"
