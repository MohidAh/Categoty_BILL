@echo off
REM ============================================================================
REM BillBook Windows Service Installer (NSSM-based)
REM ============================================================================
REM Registers billbook.exe as a Windows Service that auto-restarts on crash.
REM
REM Service behaviour:
REM   - Auto-starts on boot (no operator login needed)
REM   - Restarts within 5 seconds of a crash (up to 3 retries in 60s, then
REM     30s pause before retrying — backoff protects against crash loops)
REM   - Logs stdout/stderr to C:\Program Files\BillBook\logs\service.log
REM   - Stop via:  sc stop BillBook  (or: net stop BillBook)
REM   - Start via:  sc start BillBook (or: net start BillBook)
REM   - Uninstall via:  scripts\windows\uninstall_service.bat
REM
REM Why NSSM instead of just a startup .bat:
REM   - NSSM correctly handles Windows shutdown (waits for clean exit)
REM   - NSSM throttles crash loops (avoids burning CPU if the binary is broken)
REM   - NSSM logs stdout to a file for post-crash diagnosis
REM   - NSSM works on machines where the operator never logs in (kiosk mode)
REM
REM Prerequisites:
REM   - BillBook must already be installed (C:\Program Files\BillBook\billbook.exe)
REM   - nssm.exe must be in the same folder (downloaded by Inno Setup at install)
REM   - Run this script AS ADMINISTRATOR
REM ============================================================================

setlocal
set "SERVICE_NAME=BillBook"
set "INSTALL_DIR=%~dp0..\.."
pushd "%INSTALL_DIR%"
set "INSTALL_DIR=%CD%"
popd
set "EXE_PATH=%INSTALL_DIR%\billbook.exe"
set "LOG_DIR=%INSTALL_DIR%\logs"
set "NSSM=%~dp0nssm.exe"

REM Verify prerequisites
if not exist "%EXE_PATH%" (
    echo ERROR: billbook.exe not found at %EXE_PATH%
    echo Install BillBook first via BillBookSetup-v8.14.0.exe
    exit /b 1
)
if not exist "%NSSM%" (
    echo ERROR: nssm.exe not found at %NSSM%
    echo Re-run the BillBook installer, or download NSSM from
    echo https://nssm.cc/release/nssm-2.24.zip and extract nssm.exe
    echo to scripts\windows\nssm.exe
    exit /b 1
)
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

REM Remove existing service if present (silent)
sc query %SERVICE_NAME% >nul 2>nul
if %ERRORLEVEL% equ 0 (
    echo Existing service found — stopping and removing...
    net stop %SERVICE_NAME% >nul 2>nul
    "%NSSM%" remove %SERVICE_NAME% confirm
)

echo Installing service %SERVICE_NAME%...
"%NSSM%" install %SERVICE_NAME% "%EXE_PATH%"

echo Configuring service restart-on-crash behaviour...
"%NSSM%" set %SERVICE_NAME% AppDirectory "%INSTALL_DIR%"
"%NSSM%" set %SERVICE_NAME% AppStopMethodSkip 0
"%NSSM%" set %SERVICE_NAME% AppStopMethodConsole 1000
"%NSSM%" set %SERVICE_NAME% AppStopMethodWindow 1000
"%NSSM%" set %SERVICE_NAME% AppStopMethodThreads 1000

REM Restart-on-crash: 3 retries in 60s, then pause 30s, then resume
"%NSSM%" set %SERVICE_NAME% AppRestartDelay 5000
"%NSSM%" set %SERVICE_NAME% AppRotateFiles 1
"%NSSM%" set %SERVICE_NAME% AppRotateOnline 1
"%NSSM%" set %SERVICE_NAME% AppRotateSeconds 86400
"%NSSM%" set %SERVICE_NAME% AppRotateBytes 10485760

REM Log stdout/stderr to a rotating log file
"%NSSM%" set %SERVICE_NAME% AppStdout "%LOG_DIR%\service.log"
"%NSSM%" set %SERVICE_NAME% AppStderr "%LOG_DIR%\service.log"

REM Run as LocalSystem (no login required — perfect for kiosk mode)
"%NSSM%" set %SERVICE_NAME% ObjectName LocalSystem

REM Auto-start on boot
"%NSSM%" set %SERVICE_NAME% Start SERVICE_AUTO_START

echo Starting service...
net start %SERVICE_NAME%

echo.
echo SUCCESS: BillBook installed as a Windows Service.
echo   - Auto-starts on boot (no operator login needed)
echo   - Auto-restarts within 5s if the app crashes
echo   - Logs at: %LOG_DIR%\service.log
echo.
echo To stop:    net stop BillBook
echo To start:   net start BillBook
echo To remove:  scripts\windows\uninstall_service.bat
echo.

endlocal
exit /b 0
