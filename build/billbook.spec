# -*- mode: python ; coding: utf-8 -*-
# ============================================================================
# BillBook PyInstaller Spec — Windows code-protected build
# ============================================================================
# Purpose:
#   Bundle the entire Python application (FastAPI + app/ + static assets +
#   data dir) into a single Windows .exe that runs WITHOUT exposing source.
#
# Code-protection properties:
#   1. --onefile            : single .exe, no extracted .py files on disk
#                             (runtime extracts to %TEMP% with random dir name)
#   2. compiled .pyc only   : PyInstaller bundles .pyc, not .py — casual users
#                             cannot read application logic in a text editor
#   3. strip docstrings      : no extractable docstrings via __doc__ on
#                             compiled bytecode (limited obfuscation; for
#                             true obfuscation consider pyarmor/cython)
#   4. no source dir         : installer ships only .exe + .db template +
#                             static assets — no app/*.py files on disk
#   5. excludes              : exclude pytest/unittest/test code so reverse
#                             engineers can't probe test fixtures for hints
#
# IMPORTANT — this is "casual-user" protection, not cryptographic protection:
#   A determined reverse-engineer CAN extract .pyc from the bundle and
#   decompile with uncompyle6/decompyle3. For stronger protection, layer:
#     • PyArmor (commercial license-free for hobby use; encrypts bytecode +
#       binds to a license key or HW fingerprint) → wrap the .exe in
#       PyArmor's runtime BEFORE PyInstaller bundles it
#     • Nuitka --onefile (compiles Python to C, harder to RE than .pyc)
#   Both are documented in build/windows/STRONGER_PROTECTION.md.
# ============================================================================

import os
import sys
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

block_cipher = None  # legacy PyInstaller 3 compat; no-op in v6+

# Project root (the folder containing this .spec file's parent).
ROOT = os.path.abspath(os.path.join(SPECPATH, '..'))

# Static + data assets that must ship alongside the binary.
datas = [
    # Mount app/static/ at runtime path 'app/static/' so FastAPI StaticFiles
    # still serves the dashboard / POS / reports HTML+JS+CSS.
    (os.path.join(ROOT, 'app', 'static'), 'app/static'),
    # Empty data/ dir — created at runtime if missing (see app/config.py).
    (os.path.join(ROOT, 'data'), 'data'),
]

# Collect ALL transitively-imported submodules of these heavy packages so
# PyInstaller doesn't miss a dynamically-imported module (FastAPI does this).
hidden_imports = []
hidden_imports += collect_submodules('fastapi')
hidden_imports += collect_submodules('uvicorn')
hidden_imports += collect_submodules('starlette')
hidden_imports += collect_submodules('pydantic')
hidden_imports += collect_submodules('multipart')
hidden_imports += collect_submodules('bcrypt')
hidden_imports += collect_submodules('cryptography')
hidden_imports += collect_submodules('openpyxl')
hidden_imports += collect_submodules('reportlab')
hidden_imports += collect_submodules('fitz')           # pymupdf
hidden_imports += collect_submodules('PIL')            # pillow
hidden_imports += collect_submodules('httpx')
hidden_imports += collect_submodules('qrcode')
hidden_imports += collect_submodules('zeroconf')
hidden_imports += collect_submodules('dbfread')
# pytrends is optional — import lazily inside market_intel.py
hidden_imports += ['pytrends']

# v8.14.0: Production-hardening packages
hidden_imports += collect_submodules('google.oauth2')
hidden_imports += collect_submodules('googleapiclient')
hidden_imports += collect_submodules('google_auth_oauthlib')
hidden_imports += collect_submodules('twilio')
# sqlcipher3-binary is platform-specific — only bundle on Windows/macOS
try:
    hidden_imports += collect_submodules('sqlcipher3')
except Exception:
    pass  # not installed on the build machine — skip silently

# Our own app package — explicit so PyInstaller picks up routers/ submodules
# even though they're imported via FastAPI APIRouter at runtime, not via
# a static `import` at module-load time.
hidden_imports += collect_submodules('app')

# Exclude dev-only modules to reduce binary size + attack surface.
excluded_imports = [
    'pytest', 'unittest', 'test', 'tests',
    'IPython', 'pdb', 'profile', 'pstats',
]

a = Analysis(
    ['app/main.py'],                  # entry-point module (FastAPI app)
    pathex=[ROOT],                    # add project root to sys.path
    binaries=[],
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excluded_imports,
    win_no_prefer_redirect=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='billbook',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,                     # keep strip=False — stripped DLLs
                                     # trigger false-positive AV alerts
    upx=True,                        # compress with UPX (smaller exe)
    upx_exclude=[
        # UPX-mangling these triggers AV false positives on Windows:
        'vcruntime140.dll', 'vcruntime140_1.dll',
        'python3.dll', 'msvcp140.dll',
    ],
    runtime_tmpdir=None,             # default %TEMP% extraction
    console=True,                    # show console so operator sees uvicorn logs
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,          # see SIGNING section below
    entitlements_plist=None,
    icon=os.path.join(ROOT, 'desktop', 'icons', 'icon.ico') if os.path.exists(
        os.path.join(ROOT, 'desktop', 'icons', 'icon.ico')
    ) else None,
)

# ----------------------------------------------------------------------------
# CODE SIGNING (optional, recommended for commercial distribution)
# ----------------------------------------------------------------------------
# To sign the .exe with an Authenticode cert (so Windows SmartScreen stops
# flagging it as "unrecognized publisher"):
#   1. Acquire a code-signing cert (DigiCert / Sectigo / SSL.com — ~$200/yr).
#      EV certs require a hardware token; OV certs can be exported to .pfx.
#   2. Set env vars before running build:
#        set BILLBOOK_CODESIGN_PFX=C:\path\to\cert.pfx
#        set BILLBOOK_CODESIGN_PASS=the-pfx-password
#   3. The build script (build_windows.ps1) will call signtool.exe between
#      PyInstaller and Inno Setup stages.
#
# Without code signing, end-user Windows SmartScreen will show
# "Windows protected your PC" the first time — operator clicks
# "More info" → "Run anyway". This is normal for unsigned indie software.
# ----------------------------------------------------------------------------
