@echo off
REM ============================================================================
REM BillBook Watchdog — Fallback Auto-Restart (no NSSM needed)
REM ============================================================================
REM Use this if you can't or don't want to install NSSM. It's a simple
REM infinite loop that runs billbook.exe and immediately restarts it if
REM it exits for any reason (crash, OOM, accidental Ctrl-C).
REM
REM Limitations vs NSSM:
REM   - Doesn't auto-start on boot (operator must run this batch on login)
REM   - Doesn't throttle crash loops (a 1000-crashes/second binary will
REM     burn CPU; NSSM throttles this). But billbook.exe startup is ~2
REM     seconds, so this is rarely a problem.
REM   - No proper Windows Service integration (sc/net commands don't work)
REM
REM To make it auto-start on boot, drop a shortcut to this .bat into:
REM   C:\Users\<you>\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\
REM
REM ============================================================================

setlocal
set "INSTALL_DIR=%~dp0..\.."
pushd "%INSTALL_DIR%"
set "INSTALL_DIR=%CD%"
popd
set "EXE_PATH=%INSTALL_DIR%\billbook.exe"

if not exist "%EXE_PATH%" (
    echo ERROR: billbook.exe not found at %EXE_PATH%
    exit /b 1
)

echo BillBook Watchdog starting — press Ctrl+C to stop.
echo (Closing this window will also stop auto-restart.)
echo.

:loop
echo [%date% %time%] Starting billbook.exe...
"%EXE_PATH%"
set "EXITCODE=%ERRORLEVEL%"
echo [%date% %time%] billbook.exe exited with code %EXITCODE% — restarting in 3s...
timeout /t 3 /nobreak >nul
goto loop
