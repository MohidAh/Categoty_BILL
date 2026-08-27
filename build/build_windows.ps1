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
#     This script now probes py -3 -> python -> python3, rejects the Store
#     stub, and verifies the interpreter actually runs.
#   - `pyinstaller` is now invoked as `python -m PyInstaller` so it uses
#     the SAME interpreter we just installed deps into (no PATH games).
#
# v8.15.1 FIX (first real Windows run surfaced two blockers):
#   - $rc capture bug: native stdout lines from pip/PyInstaller leaked into
#     the Invoke-BillBookPython return value, so `if ($rc -ne 0)` compared an
#     ARRAY (PowerShell filter semantics) -> false "failed" verdicts even on
#     success, and throw-messages contained the whole pip log. Output is now
#     piped to Out-Host and only the exit code is returned.
#   - sqlcipher3-binary has NO Windows wheels on PyPI (manylinux only). The
#     requirements.txt marker is now Linux-only; on Windows the app falls
#     back to standard SQLite (by design, app/db.py) and this script prints
#     an informational NOTE instead of failing.
#   - Floor raised to Python 3.10: app/db.py uses PEP 604 unions (str | None).
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
                # v8.15.1: floor is 3.10 - app code uses PEP 604 unions
                if ($major -ge 3 -and ($major -gt 3 -or $minor -ge 10)) {
                    return @{ Exe = $c.Exe; PrefixArgs = $c.PrefixArgs; Version = "$major.$minor" }
                }
            }
        } catch { continue }
    }
    return $null
}

function Invoke-BillBookPython {
    # v8.15.1 FIX: pipe native output to Out-Host so it is DISPLAYED but never
    # enters the function's output stream. Previously every stdout line from
    # pip/PyInstaller was appended to the caller's $rc variable, so
    # `if ($rc -ne 0)` used PowerShell array-FILTER semantics (-ne on an array
    # returns non-matching ELEMENTS) -> false failures on success + garbage
    # error text. -Quiet suppresses all output for probes.
    param([string[]]$ArgList, [switch]$Quiet)
    if ($Quiet) {
        & $script:PythonInfo.Exe @($script:PythonInfo.PrefixArgs + $ArgList) 2>$null | Out-Null
    } else {
        & $script:PythonInfo.Exe @($script:PythonInfo.PrefixArgs + $ArgList) | Out-Host
    }
    return $LASTEXITCODE
}

Write-Host "`n[0/5] Locating Python..." -ForegroundColor Yellow
$PythonInfo = Resolve-BillBookPython
if (-not $PythonInfo) {
    Write-Host "ERROR: No usable Python 3.10+ found on this machine." -ForegroundColor Red
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
# (8.13.5) while the .iss define moved on - the script then failed its final
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
if ($rc -ne 0) { Write-Host "  (pip upgrade failed non-fatally - continuing)" -ForegroundColor DarkGray }
$rc = Invoke-BillBookPython @("-m", "pip", "install", "-r", "requirements.txt")
if ($rc -ne 0) { throw "pip install -r requirements.txt failed (exit $rc)" }
$rc = Invoke-BillBookPython @("-m", "pip", "install", "pyinstaller")
if ($rc -ne 0) { throw "pip install pyinstaller failed (exit $rc)" }

# v8.15.1: sqlcipher3-binary only publishes manylinux wheels on PyPI, so it
# is intentionally NOT installed on Windows (requirements.txt marker is
# Linux-only). The app falls back to standard SQLite automatically
# (app/db.py). Probe + inform - do NOT fail the build over this.
$rc = Invoke-BillBookPython @("-c", "import sqlcipher3") -Quiet
if ($rc -ne 0) {
    Write-Host "  -> NOTE: SQLCipher DB encryption is not available in Windows builds." -ForegroundColor Yellow
    Write-Host "     The app will use standard SQLite (data stored unencrypted at rest)." -ForegroundColor Yellow
    Write-Host "     This is a PyPI packaging limitation, not a build failure." -ForegroundColor Yellow
}

# --- Step 3: Run PyInstaller --------------------------------------------------
# v8.14.2: run as `python -m PyInstaller` - same interpreter as the pip above,
# so the bare-`pyinstaller`-not-on-PATH failure can never happen again.
Write-Host "`n[3/5] Running PyInstaller (code-protected onedir build)..." -ForegroundColor Yellow
Push-Location "build"
try {
    $specPath = "billbook.spec"
    $rc = Invoke-BillBookPython @("-m", "PyInstaller", $specPath, "--noconfirm", "--clean")
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
        # v8.15.1: relax EAP around native 2>&1 - under $ErrorActionPreference
        # 'Stop', PowerShell 5.1 turns iscc's first stderr line into a
        # terminating NativeCommandError. The exit CODE must decide, not stderr.
        $prevEap = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            & $isccExe "billbook.iss" 2>&1 | Tee-Object -FilePath "..\build\inno.log"
        } finally {
            $ErrorActionPreference = $prevEap
        }
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
    # does - cargo-tauri, tauri on PATH, or the npm global bin. If none is
    # found we SKIP (warning) instead of killing an otherwise-complete build.
    $tauriCli = Get-Command "cargo-tauri" -ErrorAction SilentlyContinue
    if (-not $tauriCli) { $tauriCli = Get-Command "tauri" -ErrorAction SilentlyContinue }
    $tauriCmd = $null
    if ($tauriCli) {
        $tauriCmd = @($tauriCli.Source)
    } else {
        # v8.15.1: guard npm - if Node.js is absent, & npm would throw a
        # terminating CommandNotFoundException under EAP=Stop and kill the
        # whole build at the bonus stage.
        $npmRoot = $null
        $npmCmd = Get-Command "npm" -ErrorAction SilentlyContinue
        if ($npmCmd) {
            $npmRoot = (& npm root -g 2>$null | Select-Object -First 1)
        }
        if ($npmRoot) {
            $npmTauri = Join-Path (Split-Path $npmRoot) "tauri.cmd"
            if (Test-Path $npmTauri) { $tauriCmd = @($npmTauri) }
        }
    }
    if (-not $tauriCmd) {
        Write-Host "  -> Tauri CLI not found - skipping desktop shell." -ForegroundColor DarkGray
        Write-Host "     Install with: npm install -g @tauri-apps/cli  (needs Rust for builds)" -ForegroundColor DarkGray
    } else {
        # v8.14.2 FIX: do NOT pass -p for the public key path - Tauri CLI v2
        # ignores it and always writes <private>.pub next to the private key.
        $tauriDir = "desktop\.tauri"
        if (-not (Test-Path $tauriDir)) { New-Item -ItemType Directory -Path $tauriDir -Force | Out-Null }
        if (-not (Test-Path "$tauriDir\updater-private.key")) {
            Write-Host "  -> Generating updater keypair (idempotent)..." -ForegroundColor DarkGray
            Write-Host "     (press ENTER twice when asked for a password = no password)" -ForegroundColor DarkGray
            Push-Location "desktop"
            & $tauriCmd[0] signer generate -w ".tauri\updater-private.key" --force
            Pop-Location
            # Copy the .pub to the stable documented name
            if ((Test-Path "$tauriDir\updater-private.key.pub") -and
                -not (Test-Path "$tauriDir\updater-public.key")) {
                Copy-Item "$tauriDir\updater-private.key.pub" "$tauriDir\updater-public.key"
            }
        }

        # v8.15.1: warn loudly if the pubkey in tauri.conf.json is still the
        # placeholder - such builds install fine but can NEVER auto-update.
        $confJson = Get-Content "desktop\tauri.conf.json" -Raw | ConvertFrom-Json
        if ($confJson.plugins.updater -and $confJson.plugins.updater.pubkey -like "REPLACE_WITH*") {
            Write-Host "  -> WARNING: updater.pubkey in desktop\tauri.conf.json is still the" -ForegroundColor Yellow
            Write-Host "     placeholder. Clients built like this can NEVER accept updates." -ForegroundColor Yellow
            if (Test-Path "$tauriDir\updater-public.key") {
                Write-Host "     Paste this public key into plugins.updater.pubkey:" -ForegroundColor Yellow
                (Get-Content "$tauriDir\updater-public.key") | ForEach-Object { Write-Host "       $_" -ForegroundColor White }
            }
        }

        # v8.15.1: bundle.externalBin needs the sidecar binary WITH the Rust
        # target-triple suffix, e.g.
        #   desktop\binaries\billbook_sidecar-x86_64-pc-windows-msvc.exe
        $hostTriple = "x86_64-pc-windows-msvc"
        if (Get-Command "rustc" -ErrorAction SilentlyContinue) {
            $hostLine = (& rustc -vV | Select-String "^host:" | Select-Object -First 1)
            if ($hostLine) { $hostTriple = ($hostLine.ToString() -replace "^host:\s*", "").Trim() }
        }
        $sidecarDst = "desktop\binaries\billbook_sidecar-$hostTriple.exe"
        if (-not (Test-Path $sidecarDst)) {
            if (Test-Path "dist\billbook_sidecar.exe") {
                New-Item -ItemType Directory -Path "desktop\binaries" -Force | Out-Null
                Copy-Item "dist\billbook_sidecar.exe" $sidecarDst -Force
            } else {
                # Build the sidecar here so ONE command produces everything.
                Write-Host "  -> Building the Python sidecar (PyInstaller onefile)..." -ForegroundColor DarkGray
                $rc = Invoke-BillBookPython @(
                    "-m", "PyInstaller", "app\desktop_entry.py",
                    "--onefile", "--name", "billbook_sidecar",
                    "--add-data", "app/static;app/static",
                    "--hidden-import", "uvicorn.logging",
                    "--hidden-import", "uvicorn.protocols.http.auto",
                    "--hidden-import", "uvicorn.protocols.websockets.auto",
                    "--hidden-import", "uvicorn.lifespan.on",
                    "--hidden-import", "app.main",
                    "--distpath", "build\sidecar-dist", "--workpath", "build\sidecar-work",
                    "--noconfirm"
                )
                if ($rc -eq 0 -and (Test-Path "build\sidecar-dist\billbook_sidecar.exe")) {
                    New-Item -ItemType Directory -Path "desktop\binaries" -Force | Out-Null
                    Copy-Item "build\sidecar-dist\billbook_sidecar.exe" $sidecarDst -Force
                }
            }
        }
        $skipTauriBuild = $false
        if (-not (Test-Path $sidecarDst)) {
            Write-Host "  -> Sidecar binary missing - skipping the Tauri shell." -ForegroundColor Yellow
            Write-Host "     (run scripts\build_sidecar.bat, then re-run this build)" -ForegroundColor Yellow
            $skipTauriBuild = $true
        }

        if (-not $skipTauriBuild) {
            # v8.15.1: with bundle.createUpdaterArtifacts=true the Tauri build
            # REQUIRES the signing key in the environment, otherwise it fails
            # at the updater-artifact signing step. (.env files do NOT work.)
            $privKeyAbs = Join-Path $root "desktop\.tauri\updater-private.key"
            if (Test-Path $privKeyAbs) {
                $env:TAURI_SIGNING_PRIVATE_KEY = $privKeyAbs
                if ($env:BB_UPDATER_KEY_PASSWORD) {
                    $env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD = $env:BB_UPDATER_KEY_PASSWORD
                } else {
                    $env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD = ""
                }
                Write-Host "  -> Updater signing enabled (key: $privKeyAbs)" -ForegroundColor DarkGray
                Write-Host "     (if the key has a password: set `$env:BB_UPDATER_KEY_PASSWORD first)" -ForegroundColor DarkGray
            } else {
                Write-Host "  -> WARNING: updater-private.key missing - Tauri build will fail" -ForegroundColor Yellow
                Write-Host "     at signing. Run build\windows\generate_updater_keys.ps1 first." -ForegroundColor Yellow
            }

            Push-Location "desktop"
            try {
                # v8.15.1: same EAP relaxation as the iscc step (2>&1 + Stop = trap).
                $prevEap = $ErrorActionPreference
                $ErrorActionPreference = "Continue"
                try {
                    & $tauriCmd[0] build 2>&1 | Tee-Object -FilePath "..\build\tauri.log"
                } finally {
                    $ErrorActionPreference = $prevEap
                }
                if ($LASTEXITCODE -ne 0) {
                    Write-Host "  -> Tauri build failed (see build\tauri.log) - continuing; core installer is already built." -ForegroundColor DarkYellow
                } else {
                    $nsisDirAbs = Join-Path $root "desktop\target\release\bundle\nsis"
                    Write-Host "  -> Tauri NSIS installer + .sig signature in:" -ForegroundColor Green
                    Write-Host "       $nsisDirAbs" -ForegroundColor Green
                    Write-Host "  -> Publish it with:" -ForegroundColor Green
                    Write-Host "       powershell -File build\windows\publish_release.ps1 -Owner <user> -Repo <repo> -GitHub" -ForegroundColor Green
                }
            } finally {
                Pop-Location
            }
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
Write-Host "  - build\dist\billbook\billbook.exe       (portable: copy the whole dist\billbook folder)"
Write-Host "`nCode-protection guarantees:" -ForegroundColor Cyan
Write-Host "  - No .py source files in installer"
Write-Host "  - Compiled .pyc bytecode inside _internal\ (no .py shipped)"
Write-Host "  - For stronger protection: see build\windows\STRONGER_PROTECTION.md"
