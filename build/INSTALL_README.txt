BillBook Windows Installer — Read-Me
=====================================

WHAT'S IN THE INSTALLER
-----------------------
The installer (BillBookSetup-v8.15.0.exe) contains:

  • billbook.exe            - compiled application binary (PyInstaller --onefile)
                              No Python source files (.py) are shipped.
  • _internal\              - runtime DLLs and Python packages (.pyc bytecode)
                              extracted by PyInstaller at runtime.
  • data\                   - empty data directory; SQLite DB is created here
                              on first launch.
  • start.bat, backup.bat   - operator scripts
  • INSTALL_GUIDE.md        - documentation

CODE PROTECTION GUARANTEES
--------------------------
1. No Python source code (.py) is shipped. Only compiled bytecode (.pyc)
   inside the .exe bundle.
2. The .exe is a single-file PyInstaller bundle — at runtime it extracts
   to %TEMP%\_MEIxxxx\ (random dir name) and runs from there. The user's
   machine never has a permanent copy of extracted code on disk.
3. Tauri desktop shell (separate installer) wraps the same Python binary
   inside a native Windows .exe — no Python runtime visible to the user
   at all (they see just BillBook.exe in their Program Files).
4. For stronger (commercial-grade) protection, see
   build/windows/STRONGER_PROTECTION.md — covers PyArmor encryption +
   license-key binding + Nuitka C compilation.

WHAT THE INSTALLER DOES NOT EXPOSE
----------------------------------
   ❌ No app/*.py source files in C:\Program Files\BillBook\
   ❌ No .env file (the binary reads env vars from the OS environment,
       or from %APPDATA%\BillBook\config.json — a file you create post-
       install if you want to override APP_PASSWORD).
   ❌ No git history (.git is not packaged).
   ❌ No test fixtures (tests/ directory is excluded from the bundle).

OPTIONAL FEATURES (NONE ARE REQUIRED)
-------------------------------------
BillBook ships with 5 production-hardening features that are OFF by default.
The app boots, logs in, and rings up sales without ANY of them configured.
Enable them later from Settings when (and only when) you're ready:

   1. Google Drive cloud backup   - Settings > Cloud Backup > Connect
                                    (off until you click "Connect"; no
                                    GDrive account is ever contacted)
   2. FBR POS live integration     - Settings > FBR > Configure
                                    (off until you enter FBR-issued
                                    usr_id/password/pos_id; sales are
                                    never auto-posted without your
                                    explicit toggle)
   3. Daily WhatsApp digest        - Settings > Owner Digest
                                    (off until you enter Twilio SID +
                                    owner phone; no messages are ever
                                    sent without configuration)
   4. DB at-rest encryption        - Settings > Security
                                    (off until you set BILLBOOK_DB_KEY
                                    env var or install sqlcipher3-binary;
                                    falls back to plain SQLite with a
                                    warning log line — app still runs)
   5. NSSM auto-restart service    - Run scripts\windows\install_service.bat
                                    as admin (off until you run this
                                    script; without it, the app runs
                                    as a normal user process — close
                                    the window, it stops)

You can run BillBook for years with NONE of these configured. They are
strictly opt-in features for owners who want them.

UPDATES
-------
Tauri's updater (disabled by default) lets you push signed updates that
auto-install over HTTPS. To enable, generate a keypair (see
build/windows/generate_updater_keys.ps1) and set updater.active=true
in desktop/tauri.conf.json. Updates must be signed with the private key —
clients verify with the public key baked into their installer.

SUPPORT
-------
   docs:    C:\Program Files\BillBook\USER_GUIDE.md
   email:   support@billbook.app
