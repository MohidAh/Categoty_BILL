# -*- mode: python ; coding: utf-8 -*-
# ============================================================================
# BillBook PyInstaller Spec — Windows code-protected build (ONEDIR)
# ============================================================================
# Purpose:
#   Bundle the entire Python application (FastAPI + app/ + static assets)
#   into dist\billbook\billbook.exe + dist\billbook\_internal\* that runs
#   WITHOUT exposing source.
#
# v8.15.1 FIXES (three contradictions with the rest of the build chain):
#   1. ENTRY POINT is app/desktop_entry.py, NOT app/main.py. desktop_entry
#      sets BILLBOOK_DATA_DIR next to the .exe, finds a free port, prints
#      the BILLBOOK_READY health line and runs uvicorn. main.py has no
#      __main__ run block, so building from it produced an .exe that
#      imported the app and exited immediately.
#   2. ONEDIR layout (EXE exclude_binaries=True + COLLECT). The old spec
#      passed a.binaries/a.datas into EXE (= ONEFILE), which contradicted
#      both build_windows.ps1 (checks dist\billbook\billbook.exe) and
#      billbook.iss (copies dist\billbook\_internal\*).
#   3. The dev data/ directory is NO LONGER bundled. It contained the
#      development billbook.db (831 KB of test data + logs + backups),
#      which would have shipped inside the product. The runtime data dir
#      is created next to the .exe by desktop_entry.py + app/config.py,
#      and the installer pre-creates {app}\data.
#   Also: PyInstaller 6 canonical form (no cipher/win_* legacy params).
#
# Code-protection properties:
#   1. onedir build          : billbook.exe + _internal\ runtime; no .py
#                              source files anywhere on the user's disk
#   2. compiled .pyc only    : PyInstaller bundles .pyc, not .py — casual
#                              users cannot read application logic in a
#                              text editor
#   3. no source dir         : installer ships only the .exe + _internal\ +
#                              static assets — no app/*.py files on disk
#   4. excludes              : exclude pytest/unittest/test code so reverse
#                              engineers can't probe test fixtures for hints
#
# IMPORTANT — this is "casual-user" protection, not cryptographic protection:
#   A determined reverse-engineer CAN extract .pyc from _internal\ and
#   decompile with decompyle3. For stronger protection, layer:
#     • PyArmor (encrypts bytecode + binds to a license key or HW
#       fingerprint) → wrap the .exe in PyArmor's runtime BEFORE PyInstaller
#       bundles it
#     • Nuitka --onefile (compiles Python to C, harder to RE than .pyc)
#   Both are documented in build/windows/STRONGER_PROTECTION.md.
# ============================================================================

import os
from PyInstaller.utils.hooks import collect_submodules

# Project root (the folder containing this .spec file's parent).
ROOT = os.path.abspath(os.path.join(SPECPATH, '..'))

# Static assets that must ship inside _internal\ (FastAPI StaticFiles serves
# app/static via BASE / "app" / "static" — see app/main.py line ~186).
datas = [
    (os.path.join(ROOT, 'app', 'static'), 'app/static'),
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
hidden_imports += collect_submodules('google_auth_oauthlib')
# v8.15.1: do NOT collect_submodules('googleapiclient') — it force-includes
# the ~100 MB googleapiclient.discovery_cache.documents static docs. The app
# builds the Drive service with static_discovery=False (app/cloud_backup.py)
# and its `from googleapiclient.discovery import build` / `from
# googleapiclient.http import MediaFileUpload` statements are traced by
# PyInstaller automatically. Targeted entries + documents exclusion below.
hidden_imports += ['googleapiclient.discovery', 'googleapiclient.http']
hidden_imports += collect_submodules('twilio')
# v8.15.1: sqlcipher3-binary only publishes manylinux wheels on PyPI, so it is
# only ever installed on Linux build machines. The try/except keeps this spec
# portable: on Windows the package is absent and the app falls back to
# standard SQLite at runtime (see app/db.py).
try:
    hidden_imports += collect_submodules('sqlcipher3')
except Exception:
    pass  # not installed on this build machine (expected on Windows) - skip

# Our own app package — explicit so PyInstaller picks up routers/ submodules
# even though they're imported via FastAPI APIRouter at runtime, not via
# a static `import` at module-load time.
hidden_imports += collect_submodules('app')

# Exclude dev-only modules to reduce binary size + attack surface.
# v8.15.1: also exclude heavy packages that are NOT in requirements.txt but
# can leak into the bundle from a shared/contaminated build venv (verified:
# app code never imports them, and nothing in requirements.txt needs them).
excluded_imports = [
    'pytest', 'unittest', 'test', 'tests',
    'IPython', 'pdb', 'profile', 'pstats',
    # venv-contamination guards (none of these are project dependencies):
    'boto3', 'botocore', 's3transfer',       # AWS junk from other projects
    'numba', 'llvmlite',                      # JIT toolchain
    'scipy',                                  # not required by pytrends
    'matplotlib',                             # charts are JS/reportlab-side
    'snowflake', 'snowflake-connector-python',
    # ~100 MB of offline Google API discovery docs the app never uses
    # (cloud_backup.py passes static_discovery=False):
    'googleapiclient.discovery_cache.documents',
]

a = Analysis(
    # v8.15.1: ABSOLUTE path - relative script paths resolve against the spec
    # file's directory (build\), where app/ does not exist. The old relative
    # 'app/main.py' failed with "script not found" for the same reason.
    [os.path.join(ROOT, 'app', 'desktop_entry.py')],  # frozen launcher
    pathex=[ROOT],                     # add project root to sys.path
    binaries=[],
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excluded_imports,
    noarchive=False,
)

pyz = PYZ(a.pure)

# v8.15.1: strip the ~100 MB offline Google API discovery docs. PyInstaller 6
# auto-collects package data files, and `excludes` does NOT remove those (only
# modules), so filter the datas list directly. Safe because the app builds the
# Drive service with static_discovery=False (app/cloud_backup.py) - the docs
# are never read at runtime.
_datas_before = len(a.datas)
a.datas = [d for d in a.datas
           if 'discovery_cache/documents' not in d[0].replace(chr(92), '/')]
print(f'[spec] dropped {_datas_before - len(a.datas)} Google discovery doc '
      f'data files ({_datas_before} -> {len(a.datas)} datas entries)')

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,             # ONEDIR: binaries live in _internal\
    name='billbook',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,                       # keep strip=False — stripped DLLs
                                       # trigger false-positive AV alerts
    upx=True,                          # compress with UPX (smaller exe)
    upx_exclude=[
        # UPX-mangling these triggers AV false positives on Windows:
        'vcruntime140.dll', 'vcruntime140_1.dll',
        'python3.dll', 'msvcp140.dll',
    ],
    console=True,                      # show console so operator sees uvicorn logs
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,            # see SIGNING section below
    entitlements_plist=None,
    icon=os.path.join(ROOT, 'desktop', 'icons', 'icon.ico') if os.path.exists(
        os.path.join(ROOT, 'desktop', 'icons', 'icon.ico')
    ) else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[
        'vcruntime140.dll', 'vcruntime140_1.dll',
        'python3.dll', 'msvcp140.dll',
    ],
    name='billbook',                   # -> dist\billbook\billbook.exe + _internal\
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
