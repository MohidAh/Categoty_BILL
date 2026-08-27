@echo off
REM ============================================================================
REM BillBook - Start (installed mode)
REM v8.15.1: replaces the dev start.bat in the installer. The dev script
REM created a venv and pip-installed from source - impossible in the
REM installed app, which has no Python and no source tree.
REM
REM What this does:
REM   1. Opens the dashboard in the default browser (http://127.0.0.1:8000)
REM   2. Runs billbook.exe in this window (keep it open while working;
REM      closing this window stops the server)
REM
REM The app keeps its data in the "data" folder next to this script
REM (set by app\desktop_entry.py via BILLBOOK_DATA_DIR).
REM ============================================================================

REM Wait briefly so the browser does not race the server, then open the app.
start "" /b cmd /c "timeout /t 2 /nobreak >nul & start "" http://127.0.0.1:8000"

REM Run the server in this console window (operator sees uvicorn logs).
"%~dp0billbook.exe"

REM If the server exited with an error, keep the window open so it can be read.
if errorlevel 1 (
    echo.
    echo BillBook stopped with an error. Please report this screen.
    pause
)
