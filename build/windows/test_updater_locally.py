"""
BillBook — Fake CDN for Local Updater Testing
============================================

Serves a tiny static file server on http://localhost:8085/ that mimics
a real CDN (GitHub Releases, Cloudflare R2, etc.). Hosts:

    http://localhost:8085/latest.json
    http://localhost:8085/BillBook_8.14.0_x64-setup.exe
    http://localhost:8085/BillBook_8.15.0_x64-setup.exe
    http://localhost:8085/BillBook_8.14.0_x64.msi
    http://localhost:8085/BillBook_8.15.0_x64.msi

USE CASE
--------
You want to test the Tauri auto-updater flow WITHOUT uploading to GitHub.
This script runs on your dev machine, points the running Tauri app at it,
and lets you verify v8.14 → v8.15 update actually works end-to-end.

USAGE
-----
1. Put the built installers in one folder, e.g.:
     C:\\BillBook\\releases\\
         latest.json
         BillBook_8.14.0_x64-setup.exe      <- "old" version (installed)
         BillBook_8.15.0_x64-setup.exe      <- "new" version (the update)

2. Run this script:
     cd C:\\BillBook\\releases
     python ...\\build\\windows\\test_updater_locally.py

3. Edit desktop\\tauri.conf.json updater.endpoints to:
     "http://localhost:8085/latest.json"
   (already shown in the test guide)

4. Build + install v8.14, launch it, watch it auto-detect v8.15 + offer update.

ENDPOINT TEMPLATE VARIABLES
---------------------------
Tauri's endpoints field supports these placeholders in the URL:
    {{target}}          -> e.g. "windows-x86_64"
    {{current_version}} -> e.g. "8.14.0"
    {{arch}}            -> e.g. "x86_64"

For local testing you don't need them — just serve a single static
latest.json. When you go to production (GitHub Releases), use:
    "https://github.com/USER/REPO/releases/latest/download/latest.json"
"""

from __future__ import annotations
import http.server
import socketserver
import json
import os
import sys
from pathlib import Path

PORT = 8085
HOST = "127.0.0.1"   # bind to localhost only — don't expose on LAN


class FakeCDNHandler(http.server.SimpleHTTPRequestHandler):
    """Serve files from CWD with permissive CORS so the Tauri webview can fetch."""

    def end_headers(self):
        # CORS — Tauri's updater needs to fetch from a different origin
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
        # Cache-Control — Tauri re-fetches latest.json on every launch;
        # we don't want a stale cached version masking a new release.
        self.send_header("Cache-Control", "no-store, must-revalidate")
        super().end_headers()

    def do_OPTIONS(self):
        # Respond to CORS preflight
        self.send_response(204)
        self.end_headers()

    def log_message(self, fmt, *args):
        # Custom log so you can see exactly what Tauri is requesting
        sys.stderr.write(f"[fake-cdn] {self.address_string()} - {fmt % args}\n")


def make_latest_json():
    """Generate a latest.json template if one doesn't exist in CWD."""
    cwd = Path.cwd()
    latest = cwd / "latest.json"
    if latest.exists():
        return  # don't clobber a hand-edited one

    # Find the highest-versioned setup.exe in the folder
    setup_files = sorted(cwd.glob("BillBook_*_x64-setup.exe"))
    if not setup_files:
        print("[fake-cdn] No BillBook_*_x64-setup.exe found in current dir.")
        print("[fake-cdn] Build at least one Tauri setup.exe first:")
        print("              cd desktop && cargo tauri build")
        sys.exit(1)

    # Crude version extraction: BillBook_8.15.0_x64-setup.exe -> 8.15.0
    latest_setup = setup_files[-1]
    version = latest_setup.name.split("_")[1]

    manifest = {
        "version": version,
        "notes": f"Test release v{version} — verify the updater works.",
        "pub_date": "2026-08-26T12:00:00Z",
        "platforms": {
            "windows-x86_64": {
                "signature": "REPLACE_WITH_OUTPUT_OF_TAURI_SIGNER",
                "url": f"http://{HOST}:{PORT}/{latest_setup.name}",
            }
        },
    }
    latest.write_text(json.dumps(manifest, indent=2))
    print(f"[fake-cdn] Wrote template latest.json (v{version}).")
    print("[fake-cdn] EDIT the 'signature' field with the output of:")
    print("              cargo tauri signer sign ")
    print(f"                  -k desktop/.tauri/updater-private.key "
          f"-f {latest_setup.name}")
    print("[fake-cdn] Then re-run this script.")


def main():
    os.chdir(Path(__file__).parent.resolve())  # serve the script's own dir
    # Actually we want to serve from CWD where the .exe files live
    os.chdir(Path.cwd())

    make_latest_json()

    print(f"\n[fake-cdn] Serving {Path.cwd()} on http://{HOST}:{PORT}/")
    print(f"[fake-cdn] Press Ctrl+C to stop.\n")
    print("[fake-cdn] Files in this directory:")
    for p in sorted(Path.cwd().iterdir()):
        if p.is_file():
            print(f"           {p.name}")
    print()

    with socketserver.TCPServer((HOST, PORT), FakeCDNHandler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n[fake-cdn] Stopped.")


if __name__ == "__main__":
    main()
