#!/bin/bash
# BillBook Desktop Sidecar Build Script
# =====================================
# Two packers are supported:
#
#   PACKER=pyinstaller (DEFAULT)
#     Build time: ~2-4 min.
#     Bundles Python bytecode + interpreter into one onefile exe.
#     Functionally identical to Nuitka for this app; chosen as default
#     because it cuts CI time from ~23 min to ~10 min.
#
#   PACKER=nuitka
#     Build time: ~12-20 min (compiles Python to C first).
#     Slightly harder to reverse-engineer, marginal runtime gains.
#     Use for special "thorough" releases: set the `packer` input to
#     `nuitka` when running the workflow manually.
#
# Each packer automatically falls back to the other if it is missing
# or fails, so this script never dead-ends in CI.
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"
mkdir -p data

PACKER="${PACKER:-pyinstaller}"

# v8.18.4: the Google OAuth client embedding (GDRIVE_JSON + warn_missing_creds
# + creds_args in both packers) was removed with the Drive feature.

# PyInstaller's --add-data separator is os.pathsep: ';' on Windows,
# ':' on macOS/Linux. (The old fallback used ':' everywhere, which
# silently breaks the data path on the Windows runner.)
SEP=":"
case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*) SEP=";" ;;
esac

try_pyinstaller() {
  command -v pyinstaller >/dev/null 2>&1 || return 1
  echo "[sidecar] PyInstaller build starting..."
  pyinstaller --onefile --noconfirm --name billbook_sidecar \
      --add-data "app/static${SEP}app/static" \
      --collect-submodules app \
      --hidden-import uvicorn.logging \
      --hidden-import uvicorn.protocols.http.auto \
      --hidden-import uvicorn.protocols.websockets.auto \
      --hidden-import uvicorn.lifespan.on \
      app/desktop_entry.py --distpath dist --workpath build
  echo "[sidecar] PyInstaller OK"
}

try_nuitka() {
  python -m nuitka --version >/dev/null 2>&1 || return 1
  echo "[sidecar] Nuitka build starting (slow: 12-20 min)..."
  python -m nuitka --standalone --onefile --output-dir=dist --output-filename=billbook_sidecar \
      --include-data-dir=app/static=app/static \
      --include-module=app.main \
      --include-module=app.desktop_entry --include-module=uvicorn \
      --include-package=app app/desktop_entry.py
  echo "[sidecar] Nuitka OK"
}

if [ "$PACKER" = "nuitka" ]; then
  try_nuitka || { echo "[sidecar] Nuitka failed or not installed - falling back to PyInstaller"; try_pyinstaller; }
else
  try_pyinstaller || { echo "[sidecar] PyInstaller failed or not installed - falling back to Nuitka"; try_nuitka; }
fi

# Sanity: a binary must exist at the path the workflow expects.
if [ ! -f dist/billbook_sidecar.exe ] && [ ! -f dist/billbook_sidecar ]; then
  echo "ERROR: no dist/billbook_sidecar binary was produced." && exit 1
fi
echo "[sidecar] build complete: $(ls -la dist/ | grep billbook_sidecar)"
