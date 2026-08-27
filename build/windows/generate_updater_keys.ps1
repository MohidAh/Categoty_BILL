# ============================================================================
# BillBook Tauri Updater Keypair Generator
# ============================================================================
# Purpose:
#   Generates an Ed25519 keypair used by Tauri's auto-updater. The PUBLIC key
#   is baked into every client's installer (in tauri.conf.json). The PRIVATE
#   key stays on YOUR machine and is used to sign future update bundles.
#
#   With updater disabled (default), this script is just preparing for the
#   day you turn updates on. Even with updates OFF, having the keypair ready
#   means you can flip updater.active=true in a future release without
#   forcing existing clients to re-install.
#
# Prereqs:
#   - Tauri CLI:   npm install -g @tauri-apps/cli
#                  (or: cargo install tauri-cli)
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File build\windows\generate_updater_keys.ps1
#
# Output:
#   desktop\.tauri\updater-private.key       (KEEP SECRET — never commit, never ship)
#   desktop\.tauri\updater-private.key.pub   (Tauri v2 CLI always writes the
#                                             public key next to the private
#                                             key with .pub appended)
#   desktop\.tauri\updater-public.key        (copy of the .pub for convenience
#                                             — stable documented path)
#
# v8.14.2 FIX: Tauri CLI v2 ignores the legacy -p/--public-key-path flag and
# always writes the public key to <private-key-path>.pub. The old script
# looked for desktop\.tauri\updater-public.key and crashed with
# "Get-Content : Cannot find path" even though generation succeeded.
# ============================================================================

$ErrorActionPreference = "Stop"
$desktopDir = (Resolve-Path "$PSScriptRoot\..\..\desktop").Path
$tauriDir   = Join-Path $desktopDir ".tauri"

if (-not (Test-Path $tauriDir)) {
    New-Item -ItemType Directory -Path $tauriDir -Force | Out-Null
}

$privKey    = Join-Path $tauriDir "updater-private.key"
$privKeyPub = Join-Path $tauriDir "updater-private.key.pub"   # what Tauri v2 actually writes
$pubKey     = Join-Path $tauriDir "updater-public.key"        # stable documented path (copy)

# ─── Already-have-keys check: accept either public-key layout ───────────────
$havePriv = Test-Path $privKey
$havePub  = (Test-Path $pubKey) -or (Test-Path $privKeyPub)
if ($havePriv -and $havePub) {
    Write-Host "Keypair already exists at $tauriDir" -ForegroundColor Yellow
    Write-Host "Delete those files first if you want to regenerate." -ForegroundColor Yellow
    # Still show the public key — operator may be re-running for the copy-paste
    $existingPub = if (Test-Path $pubKey) { $pubKey } else { $privKeyPub }
    Write-Host "`n=== PUBLIC KEY (paste into tauri.conf.json updater.pubkey) ===" -ForegroundColor Green
    Get-Content $existingPub
    Write-Host "=== END PUBLIC KEY ===`n" -ForegroundColor Green
    exit 0
}

Write-Host "Generating Ed25519 keypair for Tauri updater..." -ForegroundColor Cyan
Write-Host "  private: $privKey"
Write-Host "  public:  $privKeyPub  (Tauri v2 writes <private>.pub)"

# ─── Locate the Tauri CLI ───────────────────────────────────────────────────
$tauriCli = Get-Command "cargo-tauri" -ErrorAction SilentlyContinue
if (-not $tauriCli) {
    $tauriCli = Get-Command "tauri" -ErrorAction SilentlyContinue
}
if (-not $tauriCli) {
    # npm-installed CLI lands in the npm global bin dir which may not be on
    # PATH in this fresh PowerShell session — try resolving it explicitly.
    $npmRoot = & npm root -g 2>$null
    if ($LASTEXITCODE -eq 0 -and $npmRoot) {
        $npmBin = Split-Path $npmRoot
        $npmTauri = Join-Path $npmBin "tauri.cmd"
        if (Test-Path $npmTauri) {
            Write-Host "Using npm-installed Tauri CLI: $npmTauri" -ForegroundColor DarkGray
            $tauriCli = @{ Source = $npmTauri }
        }
    }
}
if (-not $tauriCli) {
    Write-Host "Tauri CLI not found. Install with:" -ForegroundColor Red
    Write-Host "  npm install -g @tauri-apps/cli" -ForegroundColor Red
    Write-Host "  (then CLOSE and REOPEN this PowerShell so PATH refreshes)" -ForegroundColor Red
    Write-Host "  OR" -ForegroundColor Red
    Write-Host "  cargo install tauri-cli" -ForegroundColor Red
    exit 1
}

# ─── Generate: Tauri v2 writes public key as <private>.pub automatically ────
# v8.14.2 FIX: do NOT pass -p for the public key path — Tauri CLI v2 ignores
# it (it's not a public-key-path flag) and always derives .pub from -w.
Push-Location $desktopDir
& $tauriCli.Source signer generate -w $privKey --force
$exitCode = $LASTEXITCODE
Pop-Location

if ($exitCode -ne 0) {
    throw "Tauri signer generate failed (exit $exitCode)"
}

# ─── Locate the generated public key (both layouts) ─────────────────────────
$generatedPub = $null
if (Test-Path $privKeyPub) { $generatedPub = $privKeyPub }
elseif (Test-Path $pubKey) { $generatedPub = $pubKey }
else {
    # Last resort: scan the .tauri dir for any *.pub created in the last minute
    $recent = Get-ChildItem $tauriDir -Filter "*.pub" -ErrorAction SilentlyContinue |
        Where-Object { $_.LastWriteTime -gt (Get-Date).AddMinutes(-1) }
    if ($recent) { $generatedPub = $recent[0].FullName }
}

if (-not $generatedPub) {
    throw "Public key not found after generation. Look in $tauriDir for a .pub file."
}

# Keep the stable documented path: copy <private>.pub → updater-public.key
if ($generatedPub -ne $pubKey) {
    Copy-Item $generatedPub $pubKey -Force
    Write-Host "`nCopied public key to stable path: $pubKey" -ForegroundColor DarkGray
}

# ─── Display the public key — operator copies it into tauri.conf.json ───────
Write-Host "`n=== PUBLIC KEY (paste into tauri.conf.json updater.pubkey) ===" -ForegroundColor Green
Get-Content $pubKey
Write-Host "=== END PUBLIC KEY ===`n" -ForegroundColor Green

Write-Host "Your keypair was generated successfully:"
Write-Host "  Private: $privKey (Keep it secret!)"
Write-Host "  Public:  $pubKey"
Write-Host ""

Write-Host "Environment variables used when SIGNING builds later:"
Write-Host "  TAURI_SIGNING_PRIVATE_KEY      (string of your private key)"
Write-Host "  TAURI_SIGNING_PRIVATE_KEY_PATH (path to your private key file)"
Write-Host "  TAURI_SIGNING_PRIVATE_KEY_PASSWORD (only if you set a password)"
Write-Host ""
Write-Host "ATTENTION: If you lose your private key OR password, you'll not be" -ForegroundColor Yellow
Write-Host "able to sign your update package and updates will not work." -ForegroundColor Yellow

Write-Host "NEXT STEPS:" -ForegroundColor Cyan
Write-Host "1. Edit desktop\tauri.conf.json" -ForegroundColor White
Write-Host "2. Set updater.pubkey to the public key shown above" -ForegroundColor White
Write-Host "3. Set updater.active to true if you want auto-updates enabled" -ForegroundColor White
Write-Host "4. BACK UP $privKey somewhere safe (USB + cloud + paper printout)" -ForegroundColor Yellow
Write-Host "5. NEVER commit $privKey to git. The desktop\.tauri\ folder is in .gitignore" -ForegroundColor Yellow
Write-Host "6. NEVER include $privKey in any installer package. The build script" -ForegroundColor Yellow
Write-Host "   excludes desktop\.tauri\ from PyInstaller + Inno Setup bundling." -ForegroundColor Yellow
