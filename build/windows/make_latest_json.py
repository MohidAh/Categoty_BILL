"""
BillBook - generate latest.json for the Tauri auto-updater (Tauri v2)
=====================================================================

WHY THIS SCRIPT EXISTS
----------------------
`cargo tauri build` (v2) produces:
    desktop/target/release/bundle/nsis/BillBook_8.15.0_x64-setup.exe
    desktop/target/release/bundle/nsis/BillBook_8.15.0_x64-setup.exe.sig

but it does NOT produce latest.json - the manifest the updater fetches
from your endpoint. (In CI, tauri-action generates it; for local builds
you run this script. The old UPDATER_TEST_GUIDE claim that "Tauri
auto-generates latest.json" was Tauri v1 behavior and is wrong for v2.)

The .sig file CONTENT (not a path, not a URL) must be pasted into the
"signature" field - that is exactly what this script does for you.

USAGE (run from the repo root)
-----------------------------
Production (GitHub Releases):
    python build\\windows\\make_latest_json.py ^
        --base-url https://github.com/OWNER/REPO/releases/download ^
        --notes "Bug fix release"

Local fake-CDN testing:
    python build\\windows\\make_latest_json.py ^
        --base-url http://127.0.0.1:8085

Output:
    build\\updater\\latest.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DESKTOP_DIR = REPO_ROOT / "desktop"
NSIS_BUNDLE = DESKTOP_DIR / "target" / "release" / "bundle" / "nsis"
MSI_BUNDLE = DESKTOP_DIR / "target" / "release" / "bundle" / "msi"
OUT_DEFAULT = REPO_ROOT / "build" / "updater" / "latest.json"


def read_version() -> str:
    conf = json.loads((DESKTOP_DIR / "tauri.conf.json").read_text(encoding="utf-8-sig"))
    return conf["version"]


def find_artifact(use_msi: bool) -> tuple[Path, Path]:
    """Return (installer_path, sig_path) for the newest build."""
    bundle_dir = MSI_BUNDLE if use_msi else NSIS_BUNDLE
    if not bundle_dir.is_dir():
        kind = "msi" if use_msi else "nsis"
        print(f"[make-latest-json] ERROR: {bundle_dir} not found.")
        print(f"[make-latest-json]        Build the Tauri shell first:")
        print(f"[make-latest-json]          cd desktop && cargo tauri build")
        print(f"[make-latest-json]        (no {kind} bundle was produced)")
        sys.exit(1)

    installers = sorted(
        [p for p in bundle_dir.iterdir()
         if p.is_file() and p.suffix in (".exe", ".msi") and not p.name.endswith(".sig")],
        key=lambda p: p.stat().st_mtime,
    )
    if not installers:
        print(f"[make-latest-json] ERROR: no installer found in {bundle_dir}")
        sys.exit(1)

    installer = installers[-1]  # newest
    sig = installer.with_name(installer.name + ".sig")
    if not sig.is_file():
        print(f"[make-latest-json] ERROR: {sig.name} is missing.")
        print("[make-latest-json]        The .sig file is created during 'cargo tauri build'")
        print("[make-latest-json]        when TAURI_SIGNING_PRIVATE_KEY is set in the")
        print("[make-latest-json]        environment. Re-run the build with:")
        print('[make-latest-json]          $env:TAURI_SIGNING_PRIVATE_KEY = "path\\to\\updater-private.key"')
        sys.exit(1)

    return installer, sig


def build_url(base_url: str, version: str, filename: str, flat: bool) -> str:
    """flat=True  -> <base>/<filename>          (plain static host / fake CDN)
    flat=False -> <base>/v<VERSION>/<filename>  (GitHub Releases pattern)"""
    base = base_url.rstrip("/")
    if flat:
        return f"{base}/{filename}"
    return f"{base}/v{version}/{filename}"


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate latest.json for the Tauri updater")
    ap.add_argument("--base-url", required=True,
                    help="Base URL of where the installer will be hosted. "
                         "The script appends /v<VERSION>/<filename>. "
                         'For local testing use "http://127.0.0.1:8085".')
    ap.add_argument("--notes", default="BillBook update.",
                    help="Release notes shown to the user (optional)")
    ap.add_argument("--out", default=str(OUT_DEFAULT),
                    help="Output path for latest.json (default: build/updater/latest.json)")
    ap.add_argument("--msi", action="store_true",
                    help="Point at the .msi bundle instead of the NSIS setup.exe (not recommended)")
    ap.add_argument("--flat", action="store_true",
                    help="Serve from a flat directory: URL = <base>/<filename> instead of "
                         "<base>/v<VERSION>/<filename>. Implied automatically for localhost.")
    args = ap.parse_args()

    version = read_version()
    installer, sig = find_artifact(args.msi)
    signature = sig.read_text(encoding="utf-8-sig").strip()

    is_local = ("127.0.0.1" in args.base_url) or ("localhost" in args.base_url)
    url = build_url(args.base_url, version, installer.name, flat=args.flat or is_local)

    manifest = {
        "version": version,
        "notes": args.notes,
        "pub_date": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "platforms": {
            "windows-x86_64": {
                "signature": signature,
                "url": url,
            }
        },
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print("[make-latest-json] latest.json written to:", out)
    print("[make-latest-json]   version   :", version)
    print("[make-latest-json]   installer :", installer.name)
    print("[make-latest-json]   signature :", signature[:32] + "...")
    print("[make-latest-json]   url       :", url)
    print()
    print("[make-latest-json] NEXT: upload these THREE files together to the same host:")
    print("  1.", installer.name)
    print("  2.", installer.name + ".sig")
    print("  3. latest.json (at the endpoint configured in desktop/tauri.conf.json)")
    print("[make-latest-json] NOTE: localhost URL - the URL is FLAT (<base>/<filename>).")
    print("  Clients must be built with the test override to allow plain http://")
    print("  (cargo tauri build --config tauri.test.conf.json).")


if __name__ == "__main__":
    main()
