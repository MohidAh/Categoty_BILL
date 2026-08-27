@echo off
REM ============================================================================
REM BillBook Windows Build Script (Batch wrapper for PowerShell)
REM ============================================================================
REM Forwards to build_windows.ps1 with execution-policy bypass.
REM Run from project root:
REM   build\build_windows.bat
REM ============================================================================

setlocal
set "SCRIPT_DIR=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%build_windows.ps1" %*
set "EXITCODE=%ERRORLEVEL%"
endlocal
exit /b %EXITCODE%
