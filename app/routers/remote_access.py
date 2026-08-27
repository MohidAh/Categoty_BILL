"""v8.1 Phase 4 — Remote Access toggle (Cloudflare Tunnel).

Endpoints:
- POST /api/remote-access/start — spawns cloudflared quick tunnel, parses URL
- POST /api/remote-access/stop — kills the tunnel process
- GET /api/remote-access/status — running? URL? uptime?

Persisted via settings so the tunnel auto-restarts on app boot if it was enabled.
"""
import os, sys, subprocess, signal, re, time, logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from .. import db
from typing import Any

router = APIRouter()
logger = logging.getLogger(__name__)

# Global state for the tunnel process (in-memory, per-process)
_tunnel_proc = None
_tunnel_url = None
_tunnel_started_at = None


def _find_cloudflared():
    """Find the cloudflared binary. Returns the path or None."""
    for path in ["cloudflared", "/usr/bin/cloudflared", "/usr/local/bin/cloudflared",
                 os.path.expanduser("~/bin/cloudflared")]:
        try:
            subprocess.run([path, "version"], capture_output=True, timeout=5)
            return path
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return None


@router.get("/api/remote-access/status")
def remote_access_status() -> Any:
    """Check if the Cloudflare Tunnel is running + return the URL + uptime."""
    global _tunnel_proc, _tunnel_url, _tunnel_started_at
    running = _tunnel_proc is not None and _tunnel_proc.poll() is None
    uptime = None
    if running and _tunnel_started_at:
        uptime = int(time.time() - _tunnel_started_at)
    # Also check the persisted setting
    enabled_in_settings = db.get_setting("remote_access_enabled", "") == "true"
    return {
        "running": running,
        "url": _tunnel_url if running else None,
        "uptime_seconds": uptime,
        "enabled": enabled_in_settings,
        "cloudflared_installed": _find_cloudflared() is not None,
    }


@router.post("/api/remote-access/start")
def remote_access_start() -> Any:
    """Start a Cloudflare quick tunnel. Returns the public HTTPS URL."""
    global _tunnel_proc, _tunnel_url, _tunnel_started_at
    # Check if already running
    if _tunnel_proc and _tunnel_proc.poll() is None:
        return {"ok": True, "url": _tunnel_url, "note": "already running"}
    # Find cloudflared
    binary = _find_cloudflared()
    if not binary:
        raise HTTPException(400, "cloudflared not installed. Install it from https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/")
    # Spawn the tunnel
    try:
        _tunnel_proc = subprocess.Popen(
            [binary, "tunnel", "--url", "http://127.0.0.1:8000", "--no-autoupdate"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
    except Exception as e:
        raise HTTPException(500, f"Failed to start cloudflared: {e}")
    # Wait for the URL to appear in output (up to 15s)
    _tunnel_url = None
    _tunnel_started_at = time.time()
    deadline = time.time() + 15
    while time.time() < deadline:
        line = _tunnel_proc.stdout.readline()
        if not line:
            if _tunnel_proc.poll() is not None:
                break
            time.sleep(0.1)
            continue
        # Look for the trycloudflare.com URL
        m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
        if m:
            _tunnel_url = m.group(0)
            break
    if not _tunnel_url:
        # Kill the process if we didn't get a URL
        if _tunnel_proc and _tunnel_proc.poll() is None:
            _tunnel_proc.kill()
        _tunnel_proc = None
        raise HTTPException(500, "Failed to get tunnel URL — cloudflared may need a moment. Try again.")
    # Persist the setting
    db.set_setting("remote_access_enabled", "true")
    db.log_activity("remote_access_started", "system", None,
                    f"Cloudflare tunnel started: {_tunnel_url}", {"url": _tunnel_url})
    return {"ok": True, "url": _tunnel_url}


@router.post("/api/remote-access/stop")
def remote_access_stop() -> Any:
    """Stop the Cloudflare tunnel."""
    global _tunnel_proc, _tunnel_url, _tunnel_started_at
    if not _tunnel_proc or _tunnel_proc.poll() is not None:
        _tunnel_proc = None
        _tunnel_url = None
        db.set_setting("remote_access_enabled", "false")
        return {"ok": True, "note": "was not running"}
    try:
        _tunnel_proc.send_signal(signal.SIGTERM)
        _tunnel_proc.wait(timeout=5)
    except Exception:
        _tunnel_proc.kill()
    _tunnel_proc = None
    _tunnel_url = None
    _tunnel_started_at = None
    db.set_setting("remote_access_enabled", "false")
    db.log_activity("remote_access_stopped", "system", None, "Cloudflare tunnel stopped", {})
    return {"ok": True}
