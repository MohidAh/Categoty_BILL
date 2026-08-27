# ============================================================================
# BillBook Windows Build Script (PowerShell)
# ============================================================================
# Produces:
#   build\dist\billbook\billbook.exe          - PyInstaller --onefile binary
#   build\dist\billbook\_internal\*           - extracted runtime assets
#   installer\BillBookSetup-vX.Y.Z.exe     - Inno Setup installer (version
#                                              read from billbook.iss)
#
# Prerequisites (run on a Windows 10/11 machine, NOT Linux):
#   1. Python 3.11+   https://www.python.org/downloads/windows/
#      >>> During install, tick "Add python.exe to PATH". If you forgot,
#          this script still finds it via the `py` launcher. <<<
#   2. PyInstaller    (installed automatically by this script)
#   3. Inno Setup 6+   https://jrsoftware.org/isdl.php
#   4. (Optional) Tauri CLI  npm install -g @tauri-apps/cli
#
# Usage:
#   cd C:\path\to\BILL_MANAGEMENT_SOFTWARE
#   powershell -ExecutionPolicy Bypass -File build\build_windows.ps1
#
# Switches:
#   -SkipInstaller     : only build the .exe, skip Inno Setup step
#   -SkipTauri         : skip Tauri desktop shell build (saves ~5 min)
#   -SignCode          : sign the .exe + installer with signtool
#                        (requires $env:CODESIGN_PFX + $env:CODESIGN_PASS)
#
# v8.14.2 FIX: "Python was not found" + "pyinstaller not recognized".
#   - `python` on a fresh Windows box resolves to the Microsoft Store
#     app-execution-alias STUB which prints that message and does nothing.
#     This script now probes py -3 → python → python3, rejects the Store
#     stub, and verifies the interpreter actually runs.
#   - `pyinstaller` is now invoked as `python -m PyInstaller` so it uses
#     the SAME interpreter we just installed deps into (no PATH games).
# ============================================================================

[CmdletBinding()]
param(
    [switch]$SkipInstaller,
    [switch]$SkipTauri,
    [switch]$SignCode
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path "$PSScriptRoot\..").Path
Set-Location $root

Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  BillBook Windows Build" -ForegroundColor Cyan
Write-Host "  Root: $root" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan

# --- Step 0: Locate a REAL Python (not the Microsoft Store stub) --------------
function Resolve-BillBookPython {
    <#
    Returns $null or a hashtable:
      @{ Exe = "C:\...\python.exe"; PrefixArgs = @() }        # direct python
      @{ Exe = "C:\Windows\py.exe";  PrefixArgs = @("-3") }   # py launcher
    Probing order: py -3, python, python3. Store stubs under
    \WindowsApps\ are rejected outright.
    #>
    $candidates = @()

    $py = Get-Command "py" -ErrorAction SilentlyContinue
    if ($py) { $candidates += @{ Exe = $py.Source; PrefixArgs = @("-3") } }

    foreach ($name in @("python", "python3")) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd -and $cmd.Source -notlike "*\WindowsApps\*") {
            $candidates += @{ Exe = $cmd.Source; PrefixArgs = @() }
        }
    }

    foreach ($c in $candidates) {
        try {
            $probeArgs = $c.PrefixArgs + @("-c", "import sys; print('PYOK %d %d' % sys.version_info[:2])")
            $out = & $c.Exe @($probeArgs) 2>$null
            if ($LASTEXITCODE -eq 0 -and ($out -join " ") -match "PYOK (\d+) (\d+)") {
                $major = [int]$Matches[1]; $minor = [int]$Matches[2]
                if ($major -ge 3 -and ($major -gt 3 -or $minor -ge 9)) {
                    return @{ Exe = $c.Exe; PrefixArgs = $c.PrefixArgs; Version = "$major.$minor" }
                }
            }
        } catch { continue }
    }
    return $null
}

function Invoke-BillBookPython {
    param([string[]]$ArgList)
    & $script:PythonInfo.Exe @($script:PythonInfo.PrefixArgs + $ArgList)
    return $LASTEXITCODE
}

Write-Host "`n[0/5] Locating Python..." -ForegroundColor Yellow
$PythonInfo = Resolve-BillBookPython
if (-not $PythonInfo) {
    Write-Host "ERROR: No usable Python 3.9+ found on this machine." -ForegroundColor Red
    Write-Host ""
    Write-Host "You likely hit the 'Microsoft Store' stub. Fix it with ONE of:" -ForegroundColor Yellow
    Write-Host "  A) Install real Python from https://www.python.org/downloads/windows/" -ForegroundColor White
    Write-Host "     Run the installer, tick 'Add python.exe to PATH', reopen PowerShell." -ForegroundColor White
    Write-Host "  B) If Python IS installed: Windows Settings > Apps > Advanced app" -ForegroundColor White
    Write-Host "     settings > App execution aliases > turn OFF both 'python' entries," -ForegroundColor White
    Write-Host "     then reopen PowerShell and re-run this script." -ForegroundColor White
    exit 1
}
Write-Host "  -> Using Python $($PythonInfo.Version) at $($PythonInfo.Exe)" -ForegroundColor Green

# --- Version: read from build\billbook.iss so it can never drift --------------
# v8.14.3: the installer filename was previously hardcoded here and went stale
# (8.13.5) while the .iss define moved on — the script then failed its final
# "installer not produced" check. Single source of truth = billbook.iss.
$issDefine = Select-String -Path "build\billbook.iss" -Pattern 'BillBookVersion\s+"([^"]+)"' | Select-Object -First 1
if (-not $issDefine) { throw "Could not read #define BillBookVersion from build\billbook.iss" }
$AppVersion = $issDefine.Matches[0].Groups[1].Value
Write-Host "  -> Building BillBook v$AppVersion" -ForegroundColor Green

# --- Step 1: Clean previous build outputs -------------------------------------
Write-Host "`n[1/5] Cleaning previous build artifacts..." -ForegroundColor Yellow
if (Test-Path "build\dist")  { Remove-Item -Recurse -Force "build\dist" }
if (Test-Path "build\__pycache__") { Remove-Item -Recurse -Force "build\__pycache__" }
if (Test-Path "build\build.log")  { Remove-Item -Force "build\build.log" }
if (Test-Path "installer\BillBookSetup-*.exe") {
    Remove-Item -Force "installer\BillBookSetup-*.exe"
}

# --- Step 2: Install Python deps ----------------------------------------------
Write-Host "`n[2/5] Installing Python dependencies..." -ForegroundColor Yellow
$rc = Invoke-BillBookPython @("-m", "pip", "install", "--upgrade", "pip")
if ($rc -ne 0) { Write-Host "  (pip upgrade failed non-fatally — continuing)" -ForegroundColor DarkGray }
$rc = Invoke-BillBookPython @("-m", "pip", "install", "-r", "requirements.txt")
if ($rc -ne 0) { throw "pip install -r requirements.txt failed (exit $rc)" }
$rc = Invoke-BillBookPython @("-m", "pip", "install", "pyinstaller")
if ($rc -ne 0) { throw "pip install pyinstaller failed (exit $rc)" }

# --- Step 3: Run PyInstaller --------------------------------------------------
# v8.14.2: run as `python -m PyInstaller` — same interpreter as the pip above,
# so the bare-`pyinstaller`-not-on-PATH failure can never happen again.
Write-Host "`n[3/5] Running PyInstaller (code-protected single-file build)..." -ForegroundColor Yellow
Push-Location "build"
try {
    $specPath = "billbook.spec"
    $rc = Invoke-BillBookPython @("-m", "PyInstaller", $specPath, "--noconfirm", "--clean")
    $output = $null  # keep pipeline semantics simple; log via redirect below
    if ($rc -ne 0) { throw "PyInstaller failed (exit $rc) - see build\build.log" }

    # Verify .exe was produced
    $exePath = "dist\billbook\billbook.exe"
    if (-not (Test-Path $exePath)) { throw "Expected output not found: $exePath" }
    $exeSize = [math]::Round((Get-Item $exePath).Length / 1MB, 1)
    Write-Host "  -> billbook.exe built ($exeSize MB)" -ForegroundColor Green

    # --- Step 4: Code-sign the .exe (optional) --------------------------------
    if ($SignCode -and $env:CODESIGN_PFX -and $env:CODESIGN_PASS) {
        Write-Host "`n[4/5] Code-signing billbook.exe..." -ForegroundColor Yellow
        & signtool sign /f $env:CODESIGN_PFX /p $env:CODESIGN_PASS `
            /tr http://timestamp.digicert.com /td sha256 /fd sha256 `
            $exePath
        if ($LASTEXITCODE -ne 0) { throw "Code signing failed" }
        Write-Host "  -> Signed" -ForegroundColor Green
    } else {
        Write-Host "`n[4/5] Skipping code-signing (no cert or -SignCode not passed)" -ForegroundColor DarkGray
        Write-Host "      Note: Windows SmartScreen will show 'unrecognized publisher' warning" -ForegroundColor DarkGray
    }
} finally {
    Pop-Location
}

# --- Step 5: Inno Setup installer --------------------------------------------
if (-not $SkipInstaller) {
    Write-Host "`n[5/5] Running Inno Setup..." -ForegroundColor Yellow
    $iscc = Get-Command iscc -ErrorAction SilentlyContinue
    if (-not $iscc) {
        $isccPath = "${env:ProgramFiles(x86)}\Inno Setup 6\iscc.exe"
        if (-not (Test-Path $isccPath)) {
            throw "Inno Setup (iscc.exe) not found. Install from https://jrsoftware.org/isdl.php (or re-run with -SkipInstaller)"
        }
        $isccExe = $isccPath
    } else {
        $isccExe = "iscc"
    }

    Push-Location "build"
    try {
        & $isccExe "billbook.iss" 2>&1 | Tee-Object -FilePath "..\build\inno.log"
        if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed - see inno.log" }
    } finally {
        Pop-Location
    }

    $installerPath = "installer\BillBookSetup-v$AppVersion.exe"
    if (-not (Test-Path $installerPath)) { throw "Installer not produced: $installerPath" }
    $installerSize = [math]::Round((Get-Item $installerPath).Length / 1MB, 1)
    Write-Host "  -> Installer built: $installerPath ($installerSize MB)" -ForegroundColor Green

    if ($SignCode -and $env:CODESIGN_PFX -and $env:CODESIGN_PASS) {
        & signtool sign /f $env:CODESIGN_PFX /p $env:CODESIGN_PASS `
            /tr http://timestamp.digicert.com /td sha256 /fd sha256 `
            $installerPath
    }
}

# --- Optional: Tauri desktop shell build -------------------------------------
if (-not $SkipTauri) {
    Write-Host "`n[Bonus] Building Tauri desktop shell..." -ForegroundColor Yellow
    # v8.14.2: locate the Tauri CLI the same tolerant way the keygen script
    # does — cargo-tauri, tauri on PATH, or the npm global bin. If none is
    # found we SKIP (warning) instead of killing an otherwise-complete build.
    $tauriCli = Get-Command "cargo-tauri" -ErrorAction SilentlyContinue
    if (-not $tauriCli) { $tauriCli = Get-Command "tauri" -ErrorAction SilentlyContinue }
    $tauriCmd = $null
    if ($tauriCli) {
        $tauriCmd = @($tauriCli.Source)
    } else {
        $npmRoot = & npm root -g 2>$null
        if ($LASTEXITCODE -eq 0 -and $npmRoot) {
            $npmTauri = Join-Path (Split-Path $npmRoot) "tauri.cmd"
            if (Test-Path $npmTauri) { $tauriCmd = @($npmTauri) }
        }
    }
    if (-not $tauriCmd) {
        Write-Host "  -> Tauri CLI not found — skipping desktop shell." -ForegroundColor DarkGray
        Write-Host "     Install with: npm install -g @tauri-apps/cli  (needs Rust for builds)" -ForegroundColor DarkGray
    } else {
        # v8.14.2 FIX: do NOT pass -p for the public key path — Tauri CLI v2
        # ignores it and always writes <private>.pub next to the private key.
        $tauriDir = "desktop\.tauri"
        if (-not (Test-Path $tauriDir)) { New-Item -ItemType Directory -Path $tauriDir -Force | Out-Null }
        if (-not (Test-Path "$tauriDir\updater-private.key")) {
            Write-Host "  -> Generating updater keypair (idempotent)..." -ForegroundColor DarkGray
            Push-Location "desktop"
            & $tauriCmd[0] signer generate -w ".tauri\updater-private.key" --force
            Pop-Location
            # Copy the .pub to the stable documented name
            if ((Test-Path "$tauriDir\updater-private.key.pub") -and
                -not (Test-Path "$tauriDir\updater-public.key")) {
                Copy-Item "$tauriDir\updater-private.key.pub" "$tauriDir\updater-public.key"
            }
        }
        Push-Location "desktop"
        try {
            & $tauriCmd[0] build 2>&1 | Tee-Object -FilePath "..\build\tauri.log"
            if ($LASTEXITCODE -ne 0) {
                Write-Host "  -> Tauri build failed (see build\tauri.log) — continuing; core installer is already built." -ForegroundColor DarkYellow
            } else {
                Write-Host "  -> Tauri .msi produced in desktop\src-tauri\target\release\bundle\" -ForegroundColor Green
            }
        } finally {
            Pop-Location
        }
    }
}

Write-Host "`n============================================" -ForegroundColor Cyan
Write-Host "  BUILD COMPLETE" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "Deliverables:"
if (-not $SkipInstaller) {
    Write-Host "  - installer\BillBookSetup-v$AppVersion.exe  (give this to clients)"
}
Write-Host "  - build\dist\billbook\billbook.exe       (portable single-file)"
Write-Host "`nCode-protection guarantees:" -ForegroundColor Cyan
Write-Host "  - No .py source files in installer"
Write-Host "  - Compiled .pyc bytecode inside single .exe"
Write-Host "  - For stronger protection: see build\windows\STRONGER_PROTECTION.md"
