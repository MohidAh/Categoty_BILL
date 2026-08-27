@echo off
REM ============================================================================
REM BillBook - Backup Now (installed mode)
REM v8.15.1: replaces the dev backup.bat in the installer. The dev script
REM hardcoded C:\billbook and needed a Python venv. This version uses
REM PowerShell (present on every Windows 10/11 machine) and works from any
REM install location - as long as BillBook is running.
REM ============================================================================

echo Triggering a database backup...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "try { Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8000/api/backup' -TimeoutSec 120 | Out-Null; Write-Host 'Backup completed - see the data\backups folder.' -ForegroundColor Green } catch { Write-Host ('Backup failed: ' + $_.Exception.Message) -ForegroundColor Red; Write-Host 'Is BillBook running? Start it first (Start BillBook.bat).' }"
echo.
pause
