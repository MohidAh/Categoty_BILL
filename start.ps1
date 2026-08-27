# PowerShell-friendly startup script for BillBook Lite
Set-Location -LiteralPath $PSScriptRoot

# Create the virtual environment if it doesn't exist.
if (-not (Test-Path -Path "venv\Scripts\python.exe")) {
    Write-Host "Virtual environment not found - creating venv..."
    $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
    if (-not $pythonCmd) {
        $pythonCmd = Get-Command py -ErrorAction SilentlyContinue
        if ($pythonCmd) {
            $pythonCmd = "$($pythonCmd.Path) -3"
        }
    }

    if (-not $pythonCmd) {
        Write-Error "Python not found. Install Python 3 and ensure 'python' or 'py' is on PATH."
        exit 1
    }

    & $pythonCmd -m venv venv
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Failed to create virtual environment."
        exit $LASTEXITCODE
    }
}

# Activate in PowerShell.
$activateScript = Join-Path $PSScriptRoot 'venv\Scripts\Activate.ps1'
if (-not (Test-Path $activateScript)) {
    Write-Error "Activation script not found: $activateScript"
    exit 1
}

. $activateScript

# Install dependencies if needed.
if (Test-Path 'requirements.txt') {
    python -m pip install -r requirements.txt
}

# Defaults
if (-not $env:HOST) { $env:HOST = '0.0.0.0' }
if (-not $env:PORT) { $env:PORT = '8000' }

$browserHost = if ($env:HOST -eq '0.0.0.0') { '127.0.0.1' } else { $env:HOST }
$browserUrl = "http://$browserHost:$($env:PORT)"

Start-Job -ScriptBlock {
    param($url)
    Start-Sleep -Seconds 2
    try { Start-Process $url -ErrorAction Stop | Out-Null } catch {}
} -ArgumentList $browserUrl | Out-Null

python -m uvicorn app.main:app --host $env:HOST --port $env:PORT
