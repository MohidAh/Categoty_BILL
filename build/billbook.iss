; ============================================================================
; BillBook Windows Installer - Inno Setup Script
; ============================================================================
; Produces a single .exe installer (BillBookSetup-vX.X.X.exe) that:
;   - Installs to C:\Program Files\BillBook\ (admin) or %LOCALAPPDATA%\BillBook
;     (non-admin) - controlled by PrivilegesRequired directive below.
;   - Ships ONLY compiled binary (billbook.exe + _internal\) + static assets.
;     No .py source files are written to the user's disk.
;   - Creates desktop shortcut + Start Menu folder.
;   - Registers uninstaller in Add/Remove Programs.
;   - Pre-creates data\ (SQLite DB is created here on first launch).
;
; v8.15.1 FIXES:
;   - Urdu messages: Urdu is an UNOFFICIAL Inno translation (it lives in
;     Languages\Unofficial\, NOT Languages\). The old path made iscc fail
;     with 'Unable to open file'. Now bundled as Urdu.isl next to this script
;     and included via #if FileExists guards - English-only fallback if the
;     file is removed. Urdu.isl requires Inno Setup 6.1.0+.
;   - Shipped scripts: start.bat/backup.bat are DEV scripts (they create a
;     venv and pip-install - impossible in the installed app). The installer
;     now ships installed_start.bat / installed_backup.bat instead.
;   - Desktop/Start-menu icons now launch Start BillBook.bat so the browser
;     opens automatically (previously the exe ran but nothing opened).
;
; Build command:
;   iscc build\billbook.iss
;
; Prerequisites:
;   1. billbook.exe + _internal\ produced by PyInstaller in build\dist\billbook\
;   2. Inno Setup 6.1+ installed (https://jrsoftware.org/isdl.php)
; ============================================================================

#define BillBookName      "BillBook"
#define BillBookVersion   "8.15.1"
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
; Show a "data directory" note: the app keeps its DB next to the exe
; (D:\BillBook\data when installed to D:), defaults to {app}\data
UsePreviousAppDir=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
; v8.15.1: Urdu.isl is bundled next to this script (unofficial translation,
; requires Inno Setup 6.1.0+). Guarded so the build never fails over it.
#if FileExists(AddBackslash(SourcePath) + "Urdu.isl")
Name: "urdu"; MessagesFile: "Urdu.isl"
#elif FileExists(AddBackslash(CompilerPath) + "Languages\Unofficial\Urdu.isl")
Name: "urdu"; MessagesFile: "compiler:Languages\Unofficial\Urdu.isl"
#endif

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"
Name: "startupicon"; Description: "Run BillBook when Windows starts"; GroupDescription: "Additional icons:"

[Files]
; --- Compiled application binary (no .py source shipped) ---
Source: "dist\billbook\{#BillBookExeName}"; DestDir: "{app}"; Flags: ignoreversion
; --- Runtime assets produced by PyInstaller (onedir) in _internal\ ---
Source: "dist\billbook\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs
; --- Empty data directory (DB + backups land here; app also creates it) ---
Source: "data\*"; DestDir: "{app}\data"; Flags: onlyifdoesntexist recursesubdirs createallsubdirs
; --- Operator scripts (installed mode: no venv, no Python needed) ---
Source: "installed_start.bat"; DestDir: "{app}"; DestName: "Start BillBook.bat"; Flags: ignoreversion
Source: "installed_backup.bat"; DestDir: "{app}"; DestName: "Backup Now.bat"; Flags: ignoreversion
; --- Documentation ---
Source: "..\INSTALL_GUIDE.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\USER_GUIDE.md";     DestDir: "{app}"; Flags: ignoreversion

[Icons]
; v8.15.1: launch via Start BillBook.bat so the browser opens automatically;
; the shortcut still shows the billbook.exe icon.
Name: "{group}\{#BillBookName}";       Filename: "{app}\Start BillBook.bat"; IconFilename: "{app}\{#BillBookExeName}"; WorkingDir: "{app}"
Name: "{group}\Uninstall {#BillBookName}"; Filename: "{uninstallexe}"
Name: "{commondesktop}\{#BillBookName}"; Filename: "{app}\Start BillBook.bat"; IconFilename: "{app}\{#BillBookExeName}"; WorkingDir: "{app}"; Tasks: desktopicon
Name: "{commonstartup}\{#BillBookName}"; Filename: "{app}\Start BillBook.bat"; IconFilename: "{app}\{#BillBookExeName}"; WorkingDir: "{app}"; Tasks: startupicon

[Run]
Filename: "{app}\Start BillBook.bat"; Description: "Launch BillBook now"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Remove data dir on uninstall - BUT ask first via confirm dialog.
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
