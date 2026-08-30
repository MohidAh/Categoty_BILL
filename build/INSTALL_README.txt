BillBook Windows Installer - Read-Me
=====================================

WHAT'S IN THE INSTALLER
-----------------------
The installer (BillBookSetup-vX.Y.Z.exe) contains:

  - billbook.exe            - compiled application launcher
                              No Python source files (.py) are shipped.
  - _internal\              - runtime DLLs and Python packages (.pyc bytecode)
                              used by billbook.exe at runtime.
  - data\                   - data directory; the SQLite database is created
                              here automatically on first launch.
  - Start BillBook.bat      - starts the app and opens the dashboard in your
                              browser (keep its window open while working)
  - Backup Now.bat          - triggers an immediate database backup
  - INSTALL_GUIDE.md,
    USER_GUIDE.md           - documentation

CODE PROTECTION GUARANTEES
--------------------------
1. No Python source code (.py) is shipped. Only compiled bytecode (.pyc)
   inside the _internal\ folder.
2. No Python installation is needed - the app carries its own runtime.
3. Tauri desktop shell (separate installer) wraps the same Python binary
   inside a native Windows app - no console window at all.
4. For stronger (commercial-grade) protection, see
   build/windows/STRONGER_PROTECTION.md - covers PyArmor encryption +
   license-key binding + Nuitka C compilation.

WHAT THE INSTALLER DOES NOT EXPOSE
----------------------------------
   x  No app/*.py source files in the install folder
   x  No .env file (the binary reads env vars from the OS environment,
       or from %APPDATA%\BillBook\config.json - a file you create post-
       install if you want to override APP_PASSWORD).
   x  No git history (.git is not packaged).
   x  No test fixtures (tests/ directory is excluded from the bundle).

OPTIONAL FEATURES (NONE ARE REQUIRED)
-------------------------------------
BillBook ships with 4 production-hardening features that are OFF by default.
The app boots, logs in, and rings up sales without ANY of them configured.
Enable them later from Settings when (and only when) you're ready:

   1. FBR POS live integration     - Settings > FBR > Configure
                                    (off until you enter FBR-issued
                                    usr_id/password/pos_id; sales are
                                    never auto-posted without your
                                    explicit toggle)
   2. Daily WhatsApp digest        - Settings > Owner Digest
                                    (off until you enter Twilio SID +
                                    owner phone; no messages are ever
                                    sent without configuration)
   3. DB at-rest encryption        - Settings > Security
                                    (SQLCipher-based; currently available
                                    on Linux servers. Windows installs run
                                    standard SQLite - the app works
                                    identically, the DB file is simply
                                    not encrypted on disk)
   4. NSSM auto-restart service    - Run scripts\windows\install_service.bat
                                    as admin (off until you run this
                                    script; without it, the app runs
                                    as a normal user process - close
                                    the window, it stops)

You can run BillBook for years with NONE of these configured. They are
strictly opt-in features for owners who want them.

UPDATES
-------
Tauri's updater (disabled by default) lets you push signed updates that
auto-install over HTTPS. To enable, generate a keypair (see
build/windows/generate_updater_keys.ps1) and set updater.active=true
in desktop/tauri.conf.json. Updates must be signed with the private key -
clients verify with the public key baked into their installer.

SUPPORT
-------
   docs:    <install folder>\USER_GUIDE.md
   email:   support@billbook.app
