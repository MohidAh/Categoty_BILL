@echo off
REM BillBook Remote Access — Cloudflare Quick Tunnel (Windows)
echo === BillBook Remote Access (Cloudflare Tunnel) ===
echo.

REM Check if cloudflared is installed
where cloudflared >nul 2>&1
if %errorlevel% neq 0 (
    echo Installing cloudflared...
    curl -L --output cloudflared.exe https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe
    echo cloudflared.exe downloaded to current directory.
    echo.
)

echo Starting Cloudflare Quick Tunnel (no account needed)...
echo This will print a public URL like: https://billbook-xxx.trycloudflare.com
echo.
echo Share this URL with your phone to connect remotely.
echo The shop PC must be running BillBook on port 8000.
echo.
echo Press Ctrl+C to stop the tunnel.
echo.

cloudflared.exe tunnel --url http://127.0.0.1:8000
