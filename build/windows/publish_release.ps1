# ============================================================================
# BillBook Tauri Release Publisher
# ============================================================================
# Purpose:
#   Publishes a Tauri desktop build as an auto-updatable release:
#     1. Verifies the NSIS installer + .sig signature exist
#     2. Generates build\updater\latest.json (make_latest_json.py)
#     3. Stages installer + .sig + latest.json in build\updater\
#     4. (Optional, -GitHub) creates a GitHub Release and uploads all three
#
# Prereqs (one-time):
#   - cargo tauri build has been run with TAURI_SIGNING_PRIVATE_KEY set
#   - gh CLI installed + logged in          (only for -GitHub)
#     winget install GitHub.cli ; gh auth login
#
# Usage (from repo root):
#   powershell -ExecutionPolicy Bypass -File build\windows\publish_release.ps1 `
#       -Owner YOUR_GITHUB_USER -Repo YOUR_REPO -Notes "Bug fixes"
#
#   With automatic GitHub release creation:
#   powershell -ExecutionPolicy Bypass -File build\windows\publish_release.ps1 `
#       -Owner YOUR_GITHUB_USER -Repo YOUR_REPO -Notes "Bug fixes" -GitHub
# ============================================================================

param(
    [Parameter(Mandatory = $true)]
    [string]$Owner,                      # GitHub user/org, e.g. "ali-hassan"

    [Parameter(Mandatory = $true)]
    [string]$Repo,                       # GitHub repo name, e.g. "billbook"

    [string]$Notes = "BillBook update.", # release notes shown to users

    [switch]$GitHub                      # also create the GitHub release via gh
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path "$PSScriptRoot\..\..").Path
Set-Location $root

# --- Read the app version from tauri.conf.json (single source of truth) ------
$confPath = "desktop\tauri.conf.json"
if (-not (Test-Path $confPath)) { throw "Not found: $confPath (run from repo root)" }
$conf = Get-Content $confPath -Raw | ConvertFrom-Json
$version = $conf.version
Write-Host "Publishing BillBook v$version" -ForegroundColor Cyan

# --- Locate the NSIS bundle + signature --------------------------------------
$nsisDir = "desktop\target\release\bundle\nsis"
$installer = Get-ChildItem $nsisDir -Filter "*-setup.exe" -ErrorAction SilentlyContinue |
    Where-Object { -not $_.Name.EndsWith(".sig") } |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $installer) {
    throw "No *-setup.exe in $nsisDir. Run the Tauri build first (with TAURI_SIGNING_PRIVATE_KEY set)."
}
$sigFile = "$($installer.FullName).sig"
if (-not (Test-Path $sigFile)) {
    throw "Missing $($installer.Name).sig - the build was not signed. Set TAURI_SIGNING_PRIVATE_KEY and rebuild."
}

# --- Generate latest.json ----------------------------------------------------
$stageDir = "build\updater"
New-Item -ItemType Directory -Path $stageDir -Force | Out-Null
$baseUrl = "https://github.com/$Owner/$Repo/releases/download"
& python "build\windows\make_latest_json.py" --base-url $baseUrl --notes $Notes --out "$stageDir\latest.json"
if ($LASTEXITCODE -ne 0) { throw "make_latest_json.py failed (exit $LASTEXITCODE)" }

# --- Stage the artifacts -----------------------------------------------------
Copy-Item $installer.FullName "$stageDir\$($installer.Name)" -Force
Copy-Item $sigFile "$stageDir\$($installer.Name).sig" -Force

Write-Host ""
Write-Host "Staged in $stageDir :" -ForegroundColor Green
Get-ChildItem $stageDir | ForEach-Object { Write-Host "  $($_.Name)" }

# --- Optional: create the GitHub release -------------------------------------
if ($GitHub) {
    $gh = Get-Command "gh" -ErrorAction SilentlyContinue
    if (-not $gh) {
        Write-Host "gh CLI not found - skipping release creation." -ForegroundColor Yellow
        Write-Host "Install it (winget install GitHub.cli), run 'gh auth login'," -ForegroundColor Yellow
        Write-Host "then create the release manually:" -ForegroundColor Yellow
        Write-Host "  gh release create v$version $stageDir\$($installer.Name) $stageDir\$($installer.Name).sig $stageDir\latest.json --title `"v$version`" --notes `"$Notes`"" -ForegroundColor White
    }
    else {
        Write-Host ""
        Write-Host "Creating GitHub release v$version ..." -ForegroundColor Cyan
        & gh release create "v$version" `
            "$stageDir\$($installer.Name)" `
            "$stageDir\$($installer.Name).sig" `
            "$stageDir\latest.json" `
            --title "v$version" --notes "$Notes"
        if ($LASTEXITCODE -ne 0) { throw "gh release create failed (exit $LASTEXITCODE)" }
        Write-Host "Release created: https://github.com/$Owner/$Repo/releases/tag/v$version" -ForegroundColor Green
    }
}
else {
    Write-Host ""
    Write-Host "Next steps:" -ForegroundColor Cyan
    Write-Host "  1. Upload the three files to https://github.com/$Owner/$Repo/releases" -ForegroundColor White
    Write-Host "     (tag v$version, title v$version)" -ForegroundColor White
    Write-Host "  2. Or re-run this script with -GitHub to do it automatically" -ForegroundColor White
}

Write-Host ""
Write-Host "Existing Tauri-shell installs will detect v$version on their next launch" -ForegroundColor Green
Write-Host "and offer the update (signature verified against the compiled-in pubkey)."
