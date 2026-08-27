@echo off
REM BillBook Desktop Sidecar Build Script (Windows)
setlocal
cd /d "%~dp0\.."
mkdir data 2>nul

echo [1/3] Attempting Nuitka build...
python -m nuitka --version >nul 2>&1
if %errorlevel%==0 (
    python -m nuitka --standalone --onefile --output-dir=dist --output-filename=billbook_sidecar.exe --include-data-dir=app/static=app/static --include-module=app.main --include-module=app.desktop_entry --include-module=uvicorn --include-package=app app/desktop_entry.py
    if %errorlevel%==0 (
        echo [2/3] Nuitka build successful!
        echo [3/3] Done.
        exit /b 0
    )
    echo Nuitka failed, falling back to PyInstaller...
)

echo [1/3] Building with PyInstaller...
pyinstaller --onefile --name billbook_sidecar --add-data "app/static;app/static" --hidden-import uvicorn.logging --hidden-import uvicorn.protocols.http.auto --hidden-import uvicorn.protocols.websockets.auto --hidden-import uvicorn.lifespan.on --hidden-import app.main app/desktop_entry.py --distpath dist --workpath build
if %errorlevel%==0 (
    echo [2/3] PyInstaller build successful!
    echo [3/3] Done.
    exit /b 0
)

echo ERROR: Both builds failed.
exit /b 1
