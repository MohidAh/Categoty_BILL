#!/bin/bash
# BillBook Desktop Sidecar Build Script (Linux/Mac)
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"
mkdir -p data

echo "[1/3] Attempting Nuitka build..."
if python -m nuitka --version >/dev/null 2>&1; then
    python -m nuitka --standalone --onefile --output-dir=dist --output-filename=billbook_sidecar \
        --include-data-dir=app/static=app/static --include-module=app.main \
        --include-module=app.desktop_entry --include-module=uvicorn \
        --include-package=app app/desktop_entry.py \
        && echo "Nuitka OK" && exit 0
    echo "Nuitka failed, falling back..."
fi

echo "[1/3] Building with PyInstaller..."
pyinstaller --onefile --name billbook_sidecar \
    --add-data "app/static:app/static" --hidden-import uvicorn.logging \
    --hidden-import uvicorn.protocols.http.auto --hidden-import uvicorn.protocols.websockets.auto \
    --hidden-import uvicorn.lifespan.on --hidden-import app.main \
    app/desktop_entry.py --distpath dist --workpath build && echo "PyInstaller OK" && exit 0

echo "ERROR: Both builds failed." && exit 1
