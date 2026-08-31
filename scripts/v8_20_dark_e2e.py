#!/usr/bin/env python3
# ═══════════════════════════════════════════════════════════════════
# v8.20 — dark-mode compatibility browser E2E (Playwright)
#
# Drives the REAL UI (login screen → shell → appearance settings) and
# asserts COMPUTED styles, proving dark mode now works with every color
# scheme end-to-end:
#
#   A. login screen follows scheme+theme (legacy vocabulary fix)
#   B. in-app: ocean + dark → canvas/ink/cards/spinner/meta all dark
#   C. accent text is LIGHT in dark (not darkened mud) and AA-readable
#   D. native color-scheme flips (scrollbars/dropdowns)
#   E. shell theme toggle flips while staying scheme-tinted
#   F. custom scheme + dark + save → persists across reload + sidecar
#
# Run: python scripts/v8_20_dark_e2e.py
# ═══════════════════════════════════════════════════════════════════
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
PORT = 8808
BASE = f"http://127.0.0.1:{PORT}"
KEY_FILE = "/home/z/my-project/download/billbook_license_private_key.pem"

PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} — {detail}")

def shade(hex_color, pct):
    n = int(hex_color[1:], 16)
    rgb = [(n >> 16) & 255, (n >> 8) & 255, n & 255]
    f = lambda c: max(0, min(255, round(c * (1 + pct) if pct < 0 else c + (255 - c) * pct)))
    return "#" + "".join(f"{f(c):02x}" for c in rgb)

def lum(h):
    n = int(h[1:], 16)
    def f(c):
        c /= 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f((n >> 16) & 255) + 0.7152 * f((n >> 8) & 255) + 0.0722 * f(n & 255)

def norm_bg(css):
    """'rgb(8, 11, 16)' -> '#080B10'"""
    m = re.match(r"rgba?\((\d+),\s*(\d+),\s*(\d+)", css.strip())
    return "#{:02X}{:02X}{:02X}".format(int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else css.strip()

OCEAN_DARK_BG = "#080B10"
OCEAN_DARK_ELEV = "#16202B"
OCEAN_DARK_TEXT = "#F0F6FC"
OCEAN_LIGHT_BG = "#F5F8FB"
CUSTOM_SEED = "#3E7BB6"
CUSTOM_DARK_BG = shade(CUSTOM_SEED, -0.86)  # deriveCustomScheme dark bg

# ── 1. temp data dir + DB + password ──────────────────────────────────
data_dir = tempfile.mkdtemp(prefix="bb820_e2e_")
os.environ["BILLBOOK_DATA_DIR"] = data_dir
sys.path.insert(0, str(PROJ))
from app import db
from app.security import hash_password
db.init()
with db.conn() as c:
    c.execute("DELETE FROM settings WHERE key='password_hash'")
    c.execute("INSERT INTO settings(key, value) VALUES(?,?)",
              ("password_hash", hash_password("testpass")))
print(f"[setup] DB at {data_dir}")

# ── 2. uvicorn (same bash call as the tests below) ────────────────────
kill_port = lambda port: subprocess.run(["pkill", "-9", "-f", f"uvicorn app.main:app --port {port}"], capture_output=True)
kill_port(PORT)
time.sleep(0.5)
server = subprocess.Popen(
    [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(PORT), "--log-level", "warning"],
    cwd=PROJ, env={**os.environ}, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
)
import urllib.request
_ready = False
for _ in range(60):
    try:
        urllib.request.urlopen(f"{BASE}/api/setup/state", timeout=1)
        _ready = True
        break
    except Exception:
        time.sleep(0.5)
assert _ready, "server never became ready"
assert server.poll() is None, "uvicorn child died — port still held by a leaked server"
print(f"[setup] server on :{PORT} (pid {server.pid})")

# ── 3. activate a license (minted for THIS machine's setup id) ────────
try:
    import requests
    st = requests.get(f"{BASE}/api/license/status", timeout=5).json()
    setup_id = st["setup_id"]
    out = subprocess.run(
        [sys.executable, str(PROJ / "scripts" / "generate_license.py"),
         "--setup-id", setup_id, "--name", "v8.20 E2E", "--days", "1",
         "--key-file", KEY_FILE],
        capture_output=True, text=True, timeout=30,
    )
    key = out.stdout
    # urlsafe base64 (may contain - and _) after the BBL1. prefix
    m = re.search(r"(BBL1\.[0-9A-Za-z+/=\-_\s]+)", key.strip())
    license_key = re.sub(r"\s+", " ", m.group(1)).strip() if m else key.strip()
    r = requests.post(f"{BASE}/api/license/activate", json={"license_key": license_key}, timeout=10)
    check("license activated for E2E", r.status_code == 200, r.text[:200])
    print(f"[setup] licensed setup_id={setup_id}")
except Exception as e:
    check("license activated for E2E", False, str(e))

# ── 4. browser checks ─────────────────────────────────────────────────
from playwright.sync_api import sync_playwright

with sync_playwright() as pw:
    browser = pw.chromium.launch()
    ctx = browser.new_context()
    # Pre-seed the appearance cache exactly like a returning user's machine
    ctx.add_init_script(
        "window.localStorage.setItem('bb-appearance', "
        f"JSON.stringify({{theme:'dark', color_scheme:'ocean', accent_color:'#3E7BB6'}}));"
    )
    page = ctx.new_page()

    # -- A. login screen: dark + ocean (the legacy-vocabulary fix) -----
    page.goto(f"{BASE}/login", wait_until="networkidle")
    body_bg = page.evaluate("getComputedStyle(document.body).backgroundColor")
    check("A1 login body is OCEAN dark canvas", norm_bg(body_bg) == OCEAN_DARK_BG, body_bg)
    ink = page.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--ink').trim()")
    check("A2 login --ink is dark-theme text", ink.upper() == OCEAN_DARK_TEXT, ink)
    cs = page.evaluate("getComputedStyle(document.documentElement).colorScheme")
    check("A3 native color-scheme is dark", cs == "dark", cs)

    # -- login → launcher --------------------------------------------
    page.fill("#p", "testpass")
    page.click(".login-btn")
    page.wait_for_selector(".launcher-root", timeout=20000)
    page.wait_for_timeout(600)
    launcher_bg = page.evaluate("getComputedStyle(document.body).backgroundColor")
    # Fresh DB → server defaults (warm/light). initAppearance() makes the
    # SERVER authoritative after login, so the launcher shows the account's
    # appearance — it must not be hardcoded and must follow the sync.
    check("A4 launcher follows the account appearance (server sync)",
          norm_bg(launcher_bg) == "#FAF9F5", launcher_bg)

    # -- B. appearance page: ocean + dark via the REAL UI --------------
    page.goto(f"{BASE}/#/settings/appearance")
    page.wait_for_selector(".ap-scheme-card[data-scheme='ocean']", timeout=20000)
    page.wait_for_timeout(400)
    page.click(".appearance-theme-card[data-theme='dark']")
    page.click(".ap-scheme-card[data-scheme='ocean']")
    page.wait_for_timeout(300)

    root = page.evaluate("""() => {
        const cs = getComputedStyle(document.documentElement);
        return {
          canvas: cs.getPropertyValue('--canvas').trim(),
          ink: cs.getPropertyValue('--ink').trim(),
          primary: cs.getPropertyValue('--primary').trim(),
          primaryText: cs.getPropertyValue('--primary-text').trim(),
          colorScheme: cs.colorScheme,
          meta: (document.querySelector('meta[name=theme-color]')||{}).content,
        };
    }""")
    check("B1 --canvas is ocean dark", root["canvas"].upper() == OCEAN_DARK_BG, root["canvas"])
    check("B2 --ink is ocean dark text", root["ink"].upper() == OCEAN_DARK_TEXT, root["ink"])
    check("B3 meta theme-color follows dark canvas", (root["meta"] or "").upper() == OCEAN_DARK_BG, root["meta"])
    check("B4 native color-scheme dark in app", root["colorScheme"] == "dark", root["colorScheme"])

    card_bg = page.evaluate(
        "() => { const el = document.querySelector('.card, .kpi');"
        "return el ? getComputedStyle(el).backgroundColor : 'none'; }")
    check("B5 card element renders dark surface",
          norm_bg(card_bg) in (OCEAN_DARK_BG, OCEAN_DARK_ELEV, "#0E141B"), card_bg)

    # -- C. accent text light + readable in dark ----------------------
    pt = root["primaryText"]
    check("C1 accent text LIGHTENS in dark", lum(pt) > lum(root["primary"]),
          f"{root['primary']} -> {pt}")
    check("C2 accent text AA floor (>=3) on dark bg", (lum(pt) + 0.05) / (lum(OCEAN_DARK_BG) + 0.05) >= 3.0,
          f"ratio={( (lum(pt)+0.05)/(lum(OCEAN_DARK_BG)+0.05) ):.2f}")

    spinner = page.evaluate(
        "() => { const el = document.querySelector('.loading-spinner, .spinner');"
        "return el ? getComputedStyle(el).borderTopColor || getComputedStyle(el).borderColor : 'none'; }")
    check("C3 loading spinner follows accent", norm_bg(spinner) in ("#3E7BB6", "#3e7b66", "#3E7BB6".upper()), spinner)

    # -- D. save + reload persistence (custom scheme + dark) -----------
    page.click(".ap-scheme-card[data-scheme='custom']")
    page.wait_for_timeout(200)
    page.fill("#ap-custom-base-text", CUSTOM_SEED)
    page.dispatch_event("#ap-custom-base-text", "input")
    page.wait_for_timeout(300)
    page.click("#ap-save-btn")
    page.wait_for_timeout(800)
    page.reload(wait_until="networkidle")
    page.wait_for_selector(".ap-scheme-card[data-scheme='custom'].selected, .ap-scheme-card", timeout=20000)
    page.wait_for_timeout(800)

    after = page.evaluate("""() => {
        const cs = getComputedStyle(document.documentElement);
        return {
          theme: document.documentElement.getAttribute('data-theme'),
          canvas: cs.getPropertyValue('--canvas').trim(),
          colorScheme: cs.colorScheme,
          meta: (document.querySelector('meta[name=theme-color]')||{}).content,
        };
    }""")
    check("D1 reload keeps dark theme", after["theme"] == "dark", after["theme"])
    check("D2 reload keeps CUSTOM dark canvas", after["canvas"].upper() == CUSTOM_DARK_BG.upper(),
          f"{after['canvas']} != {CUSTOM_DARK_BG}")
    check("D3 native color-scheme survives reload", after["colorScheme"] == "dark", after["colorScheme"])
    check("D4 meta theme-color = custom dark canvas", (after["meta"] or "").upper() == CUSTOM_DARK_BG.upper(),
          after["meta"])

    # -- D5. the launcher (separate surface) follows the SAVED dark scheme --
    page.goto(f"{BASE}/#/launcher")
    page.wait_for_selector(".launcher-root", timeout=20000)
    page.wait_for_timeout(1000)
    launcher_after = page.evaluate("getComputedStyle(document.body).backgroundColor")
    check("D5 launcher renders the saved custom dark canvas",
          norm_bg(launcher_after) == CUSTOM_DARK_BG.upper(), launcher_after)

    page.goto(f"{BASE}/#/dashboard")
    page.wait_for_selector("#shell-theme-toggle", timeout=20000)
    page.wait_for_timeout(600)
    check("B0 shell renders inside app", page.evaluate("!!document.querySelector('#shell-theme-toggle')"))

    # -- E. shell toggle flips theme but keeps the scheme --------------
    page.click("#shell-theme-toggle")
    page.wait_for_timeout(400)
    flipped = page.evaluate("""() => {
        const cs = getComputedStyle(document.documentElement);
        return { theme: document.documentElement.getAttribute('data-theme'),
                 canvas: cs.getPropertyValue('--canvas').trim(),
                 colorScheme: cs.colorScheme };
    }""")
    check("E1 toggle flips to light", flipped["theme"] == "light", flipped["theme"])
    check("E2 light canvas is the CUSTOM light bg (not warm cream)",
          flipped["canvas"].upper() not in ("#FAF9F5", "#FFFFFF") and lum(flipped["canvas"]) > 0.8,
          flipped["canvas"])
    check("E3 native color-scheme flips to light", flipped["colorScheme"] == "light", flipped["colorScheme"])

    # restore dark for the API assertions below
    page.click("#shell-theme-toggle")
    page.wait_for_timeout(400)

    browser.close()

# ── 5. server-side persistence of what the UI saved ───────────────────
try:
    import requests as rq
    s = rq.Session()
    s.post(f"{BASE}/api/login", json={"password": "testpass"}, timeout=5)
    cfg = s.get(f"{BASE}/api/appearance", timeout=5).json()
    check("F1 server persisted dark", cfg["theme"] == "dark", cfg)
    check("F2 server persisted custom scheme", cfg["color_scheme"] == "custom", cfg)
    check("F3 server persisted custom seed", cfg["custom_scheme_base"].upper() == CUSTOM_SEED, cfg)
    sidecar = Path(data_dir) / "appearance.json"
    check("F4 sidecar mirrors dark for the Tauri splash",
          sidecar.exists() and json.loads(sidecar.read_text())["theme"] == "dark",
          f"exists={sidecar.exists()} dir={os.listdir(data_dir)}")
    if sidecar.exists():
        check("F5 sidecar mirrors custom scheme", json.loads(sidecar.read_text())["color_scheme"] == "custom",
              sidecar.read_text()[:120])
finally:
    # ALWAYS release the port — a leaked server hijacks the next run
    server.terminate()
    try:
        server.wait(timeout=10)
    except Exception:
        server.kill()
    kill_port(PORT)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
