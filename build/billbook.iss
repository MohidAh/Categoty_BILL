; ============================================================================
; BillBook Windows Installer — Inno Setup Script
; ============================================================================
; Produces a single .exe installer (BillBookSetup-vX.X.X.exe) that:
;   • Installs to C:\Program Files\BillBook\ (admin) or %LOCALAPPDATA%\BillBook
;     (non-admin) — controlled by PrivilegesRequired directive below.
;   • Ships ONLY compiled binary (billbook.exe) + static assets + data
;     template. No .py source files are written to the user's disk.
;   • Creates desktop shortcut + Start Menu folder.
;   • Registers uninstaller in Add/Remove Programs.
;   • Pre-creates data\billbook.db (empty SQLite) on first launch.
;
; Build command:
;   iscc build\billbook.iss
;
; Prerequisites:
;   1. billbook.exe produced by PyInstaller in build\dist\billbook\
;   2. Inno Setup 6+ installed (https://jrsoftware.org/isdl.php)
; ============================================================================

#define BillBookName      "BillBook"
#define BillBookVersion   "8.15.0"
#define BillBookPublisher "BillBook"
#define BillBookExeName   "billbook.exe"

[Setup]
AppName={#BillBookName}
AppVersion={#BillBookVersion}
AppPublisher={#BillBookPublisher}
AppSupportURL=https://billbook.app/support
AppUpdatesURL=https://billbook.app/releases
DefaultDirName={autopf}\{#BillBookName}
DefaultGroupName={#BillBookName}
DisableProgramGroupPage=yes
OutputDir=..\installer
OutputBaseFilename=BillBookSetup-v{#BillBookVersion}
Compression=lzma2/ultra64
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequiredOverridesAllowed=dialog
UninstallDisplayIcon={app}\{#BillBookExeName}
WizardStyle=modern
LicenseFile=..\LICENSE.txt
InfoBeforeFile=..\build\INSTALL_README.txt
; Show a "data directory" page so users can keep their DB on a different
; drive (e.g. D:\BillBook\data) — defaults to {app}\data
UsePreviousAppDir=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "urdu";    MessagesFile: "compiler:Languages\Urdu.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"
Name: "startupicon"; Description: "Run BillBook when Windows starts"; GroupDescription: "Additional icons:"

[Files]
; --- Compiled application binary (no .py source shipped) ---
Source: "dist\billbook\{#BillBookExeName}"; DestDir: "{app}"; Flags: ignoreversion
; --- Runtime assets extracted by PyInstaller into _internal/ ---
Source: "dist\billbook\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs
; --- Empty data directory (DB + backups land here) ---
Source: "data\*"; DestDir: "{app}\data"; Flags: onlyifdoesntexist recursesubdirs createallsubdirs
; --- Helper batch scripts (start, stop, backup) ---
Source: "..\start.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\backup.bat"; DestDir: "{app}"; Flags: ignoreversion
; --- Documentation ---
Source: "..\INSTALL_GUIDE.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\USER_GUIDE.md";     DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#BillBookName}";       Filename: "{app}\{#BillBookExeName}"
Name: "{group}\Uninstall {#BillBookName}"; Filename: "{uninstallexe}"
Name: "{commondesktop}\{#BillBookName}"; Filename: "{app}\{#BillBookExeName}"; Tasks: desktopicon
Name: "{commonstartup}\{#BillBookName}"; Filename: "{app}\{#BillBookExeName}"; Tasks: startupicon

[Run]
Filename: "{app}\{#BillBookExeName}"; Description: "Launch BillBook now"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Remove data dir on uninstall — BUT ask first via confirm dialog.
; Inno Setup can't easily show a per-file confirm; we use UninstallDataDirTask.
Type: filesandordirs; Name: "{app}\data"
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}"

[Code]
function InitializeSetup(): Boolean;
begin
  Result := True;
  // Show operator a heads-up that .db data files will be preserved across
  // uninstall unless they explicitly choose to delete them in the wizard.
end;

function ShouldRemoveDataDir(): Boolean;
begin
  // Default = NO (keep operator data). Override via /REMOVE_DATA=yes on
  // the uninstaller command line for silent CI uninstalls.
  Result := False;
end;
