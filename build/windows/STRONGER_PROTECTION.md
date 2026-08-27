BillBook — Stronger Code Protection Guide
=========================================

This document explains how to upgrade the default PyInstaller build to
commercial-grade code protection, so even a determined reverse-engineer
cannot extract your application logic.

Default protection level (build/billbook.spec):
  - .py to .pyc compiled bytecode inside single .exe
  - Casual users can't read code
  - A skilled reverse-engineer CAN extract .pyc and decompile with
    `uncompyle6` or `decompyle3` (~30 minutes of effort)

For higher protection, choose ONE of these layered strategies:

================================================================
STRATEGY A - PyArmor (recommended for most businesses)
================================================================
PyArmor encrypts .pyc files at rest and decrypts at runtime using a
native (C) loader. You can ALSO bind the runtime to a license key or
to a hardware fingerprint (motherboard SN, CPU ID, MAC address).

1. Install:
     pip install pyarmor

2. Generate obfuscated bundle:
     cd C:\BillBook
     pyarmor gen --output dist_pyarmor --recursive app
     This produces dist_pyarmor\app\*.py (obfuscated) - these are NOT
     readable .py files, they're thin shims that load encrypted bytecode
     via the PyArmor runtime.

3. Modify PyInstaller spec to use the obfuscated app/:
   In build/billbook.spec, change entry-point:
     scripts=['dist_pyarmor/app/main.py']
   And add the PyArmor runtime as a binary:
     binaries=[(os.path.join(ROOT, 'dist_pyarmor', 'pyarmor_runtime_000000'),
                'pyarmor_runtime')]

4. (Optional) License-key binding:
     pyarmor gen --bind-out-license BillBook.lic \
                --bind-disk-sn XXXXXXX --bind-mac XX:XX:XX:XX:XX:XX \
                --expire-date 2026-12-31 \
                --recursive app
   This generates a per-machine license file BillBook.lic that must be
   placed next to the .exe - runtime refuses to launch without it.

5. Build the rest as usual:
     pyinstaller build\billbook.spec

Cost: PyArmor is free for open-source / individual use. Business license
(~$60/yr) is required for commercial distribution.

================================================================
STRATEGY B - Nuitka (compiles Python to C -> true native binary)
================================================================
Nuitka translates Python source to C and compiles with MSVC. The output
.exe contains native machine code, NOT .pyc bytecode - there's no .pyc
to extract or decompile.

1. Install:
     pip install nuitka
     (also requires MSVC Build Tools - see nuitka download page)

2. Build:
     cd C:\BillBook
     python -m nuitka --standalone --onefile --enable-plugin=anti-bloat \
        --include-package=app --include-data-dir=app/static=app/static \
        --windows-icon-from-ico=desktop/icons/icon.ico \
        --output-dir=build\nuitka \
        app/main.py

Output: build\nuitka\main.exe (~80-150 MB; larger than PyInstaller
because it inlines the entire CPython runtime, but no .pyc anywhere).

Cost: Nuitka is free (open-source). Commercial support available.

================================================================
STRATEGY C - PyArmor + PyInstaller + Inno Setup (default + license key)
================================================================
Most businesses want this combination:
  1. PyArmor obfuscates the code + binds to a per-customer license file
  2. PyInstaller bundles the obfuscated code into a single .exe
  3. Inno Setup wraps it in a polished installer
  4. You (the vendor) generate per-customer license files from a server
     and email them after purchase

Template license-issuance server: see build/windows/license_server.py
(not shipped with the installer - run on your side, never expose to
end users).
