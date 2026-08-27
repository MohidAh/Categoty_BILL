@echo off
REM Change to the directory this script is located in.
cd /d "%~dp0"

REM Create the venv if needed.
if not exist "venv\Scripts\python.exe" (
    echo Virtual environment not found - creating venv...
    python -m venv venv 2>nul
    if errorlevel 1 (
        echo 'python' command failed or not found; trying 'py -3'...
        py -3 -m venv venv 2>nul
        if errorlevel 1 (
            echo Python not found or venv creation failed.
            echo Install Python 3 ^(https://www.python.org/downloads/^)
            echo and ensure 'python' or 'py' is available on PATH.
            exit /b 1
        )
    )
)

REM Activate the venv.
call "venv\Scripts\activate.bat"

REM Use the venv python; if not available, choose python or py -3.
set "PYTHONEXE=venv\Scripts\python.exe"
if not exist "%PYTHONEXE%" (
    set "PYTHONEXE=python"
    where python >nul 2>nul || set "PYTHONEXE=py -3"
)

REM Install dependencies.
if exist "requirements.txt" (
    "%PYTHONEXE%" -m pip install -r requirements.txt
)

REM Default host and port if not set.
if "%HOST%"=="" set "HOST=0.0.0.0"
if "%PORT%"=="" set "PORT=8000"
if "%HOST%"=="0.0.0.0" (
    set "BROWSER_HOST=127.0.0.1"
) else (
    set "BROWSER_HOST=%HOST%"
)

REM Open the app in the browser once the server starts.
start "" "http://%BROWSER_HOST%:%PORT%"

REM Run the app.
REM v8.13.1: Removed --reload (dev-only flag; adds CPU overhead + security surface in production)
"%PYTHONEXE%" -m uvicorn app.main:app --host %HOST% --port %PORT%
