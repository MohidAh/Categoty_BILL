#!/bin/bash
# BillBook Remote Access — Cloudflare Quick Tunnel
# Creates a free trycloudflare.com tunnel (no account needed).
# For a persistent named tunnel, see the alternative below.

set -e

echo "=== BillBook Remote Access (Cloudflare Tunnel) ==="
echo ""

# Check if cloudflared is installed
if ! command -v cloudflared &>/dev/null; then
    echo "Installing cloudflared..."
    # Linux/Mac
    if [[ "$OSTYPE" == "linux-gnu"* ]] || [[ "$OSTYPE" == "darwin"* ]]; then
        curl -L --output /tmp/cloudflared https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64
        chmod +x /tmp/cloudflared
        sudo mv /tmp/cloudflared /usr/local/bin/cloudflared 2>/dev/null || mv /tmp/cloudflared ./cloudflared
    else
        echo "Please install cloudflared manually: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"
        exit 1
    fi
fi

echo "Starting Cloudflare Quick Tunnel (no account needed)..."
echo "This will print a public URL like: https://billbook-xxx.trycloudflare.com"
echo ""
echo "Share this URL with your phone to connect remotely."
echo "The shop PC must be running BillBook on port 8000."
echo ""
echo "Press Ctrl+C to stop the tunnel."
echo ""

# Quick tunnel — no account, random URL, free
cloudflared tunnel --url http://127.0.0.1:8000

# ── Alternative: Named tunnel with a free Cloudflare account ──
# For a persistent URL (e.g., billbook.your-domain.com):
#   1. Create a free Cloudflare account
#   2. cloudflared tunnel login
#   3. cloudflared tunnel create billbook
#   4. cloudflared tunnel route dns billbook billbook.your-domain.com
#   5. cloudflared tunnel run billbook
# This gives you a stable URL that survives restarts.
