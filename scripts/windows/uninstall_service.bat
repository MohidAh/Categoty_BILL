@echo off
REM ============================================================================
REM BillBook Windows Service Uninstaller
REM ============================================================================

setlocal
set "SERVICE_NAME=BillBook"
set "NSSM=%~dp0nssm.exe"

if not exist "%NSSM%" (
    echo ERROR: nssm.exe not found at %NSSM%
    exit /b 1
)

sc query %SERVICE_NAME% >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo No BillBook service found — nothing to remove.
    exit /b 0
)

echo Stopping service...
net stop %SERVICE_NAME% >nul 2>nul

echo Removing service...
"%NSSM%" remove %SERVICE_NAME% confirm

echo Done. BillBook service removed.
echo (BillBook files are still in C:\Program Files\BillBook\ — uninstall via
echo  Add/Remove Programs to remove them too.)

endlocal
exit /b 0
