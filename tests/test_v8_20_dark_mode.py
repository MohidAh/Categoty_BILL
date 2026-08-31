# ═══════════════════════════════════════════════════════════════════
# v8.20 — dark-mode compatibility tests
#
# User report: "need to make it dark mode compatible as well" — the
# color-scheme engine (v8.18.7/v8.19) restyled LIGHT mode fully, but:
#   1. _legacyFromTokens() always derived the legacy base.css primitives
#      from the LIGHT token set, so dark mode painted the login/license
#      screens and ~60 legacy components LIGHT under every non-warm
#      scheme (split-brained dark mode).
#   2. The accent family (--primary-hover/-text, --coral-text, …) only
#      ever DARKENED — in dark mode that is mud on a dark canvas.
#   3. index.html's in-app boot splash was hardcoded cream, and the dark
#      pre-paint override used warm constants (wrong under other schemes).
#   4. No native color-scheme → light scrollbars/dropdowns in dark mode.
#
# Verified here:
#   - node smoke test (186 checks) exercising the real applyAppearance()
#     under a DOM shim across all schemes × themes
#   - static guards on the boot surfaces (index.html, base.css)
#   - backend: dark theme + custom scheme round-trip incl. sidecar
# ═══════════════════════════════════════════════════════════════════
import json
import re
import subprocess
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))


def _client():
    from fastapi.testclient import TestClient
    from app import main
    from test_helpers import setup_test_db_with_password, login_client, cleanup
    test_dir = setup_test_db_with_password(prefix="bb820_")
    client = TestClient(main.app)
    login_client(client)
    return test_dir, client, cleanup


# ── The real engine: node smoke over the actual utils.js ───────────────

def test_dark_mode_node_smoke():
    """All 186 scheme×theme checks from the live applyAppearance engine."""
    r = subprocess.run(
        ["node", str(PROJ / "scripts" / "v8_20_dark_mode_smoke.mjs")],
        capture_output=True, text=True, timeout=120,
    )
    assert r.returncode == 0, f"smoke failed:\n{r.stdout[-3000:]}\n{r.stderr[-2000:]}"
    m = re.search(r"(\d+) passed, (\d+) failed", r.stdout)
    assert m, f"smoke summary missing:\n{r.stdout[-2000:]}"
    assert int(m.group(2)) == 0
    total = int(m.group(1))
    assert total >= 150, f"expected a broad matrix, only {total} checks ran"
    print(f"✓ test_dark_mode_node_smoke ({total} checks)")


# ── Static guards on the dark boot surfaces ────────────────────────────

def test_index_boot_splash_is_token_driven():
    """The in-app splash must follow --canvas/--coral in BOTH themes."""
    html = (PROJ / "app" / "static" / "index.html").read_text()
    assert re.search(r'id="boot-splash"[^>]*var\(--canvas,', html), \
        "boot splash background must use var(--canvas, fallback)"
    assert "var(--coral,#cc785c)" in html, "splash spinner must use var(--coral)"
    assert "_bs.style.background='#181715'" not in html, \
        "the old hardcoded warm-dark splash override must be gone (wrong under non-warm schemes)"


def test_index_inline_accent_is_theme_aware():
    """First-paint accent derivation must lighten in dark (match utils.js)."""
    html = (PROJ / "app" / "static" / "index.html").read_text()
    m = re.search(r"var dk = c\.theme === 'dark';(.{0,600}?)var vars = \{", html, re.S)
    assert m, "dark flag in inline boot script not found"
    body = m.group(1)
    assert "dk ? 0.16 : -0.14" in body, "hover must lighten in dark"
    assert "dk ? 0.30 : -0.22" in body, "text must lighten in dark"


def test_base_css_native_color_scheme():
    """Native widgets (scrollbars, dropdowns) must follow the theme."""
    css = (PROJ / "app" / "static" / "styles" / "base.css").read_text()
    root = re.search(r":root\s*\{(.*?)\}", css, re.S).group(1)
    dark = re.search(r'\[data-theme="dark"\]\s*\{(.*?)\}', css, re.S).group(1)
    assert "color-scheme: light" in root
    assert "color-scheme: dark" in dark


def test_select_options_follow_scheme_in_light():
    """Light-mode dropdown options must not be hardcoded #fff."""
    css = (PROJ / "app" / "static" / "css" / "design-system.css").read_text()
    assert not re.search(r"select option\s*\{\s*background-color:\s*#fff", css), \
        "select option background must use var(--surface)"


def test_legacy_derivation_is_active_theme_aware():
    """_legacyFromTokens must take the active-theme tokens (3-arg call)."""
    src = (PROJ / "app" / "static" / "js" / "utils.js").read_text()
    assert "function _legacyFromTokens(active, other, isDark)" in src, \
        "legacy derivation must know which theme is active"
    assert re.search(r"_legacyFromTokens\(toks,.*scheme\.light.*scheme\.dark.*c\.theme === 'dark'\)", src), \
        "applyAppearance must pass the ACTIVE tokens + dark flag"
    # the dark branch maps the legacy vocabulary onto the active dark set
    dark_branch = re.search(r"if \(isDark\) \{\s*return \{(.*?)\};", src, re.S)
    assert dark_branch, "dark branch of _legacyFromTokens missing"
    body = dark_branch.group(1)
    for key in ("canvas: active.bg", "ink: active.text", "surfaceCard: active.elevated"):
        assert key in body, f"dark legacy branch must map {key}"


def test_accent_derivation_is_theme_aware():
    """The accent family must flip shade direction in dark mode."""
    src = (PROJ / "app" / "static" / "js" / "utils.js").read_text()
    m = re.search(r"const dark = c\.theme === 'dark';(.{0,1200}?)const vars = \{", src, re.S)
    assert m, "theme-aware accent block not found"
    body = m.group(1)
    assert "dark ? 0.16 : -0.14" in body, "hover must lighten in dark"
    assert "dark ? 0.30 : -0.22" in body, "text must lighten in dark"
    assert "_ensureAA(" in body, "accent text must go through the AA guard"
    assert "'--on-primary': onAccent" in src, "on-accent text must be luminance-driven"


# ── Backend: dark + custom scheme persistence (the combo the user uses) ─

def test_dark_custom_scheme_round_trip_and_sidecar():
    test_dir, client, cleanup = _client()
    try:
        r = client.post("/api/appearance", json={
            "theme": "dark", "color_scheme": "custom",
            "custom_scheme_base": "#3E7BB6", "accent_color": "#3E7BB6",
        })
        assert r.status_code == 200, r.text
        cfg = client.get("/api/appearance").json()
        assert cfg["theme"] == "dark"
        assert cfg["color_scheme"] == "custom"
        assert cfg["custom_scheme_base"].lower() == "#3e7bb6"
        # the splash sidecar must carry the dark theme too
        from app import config
        snap = json.loads((Path(config.DATA) / "appearance.json").read_text())
        assert snap["theme"] == "dark", "splash must boot dark, not flash light"
        assert snap["color_scheme"] == "custom"
        print("✓ test_dark_custom_scheme_round_trip_and_sidecar")
    finally:
        cleanup(test_dir)


if __name__ == "__main__":
    test_dark_mode_node_smoke()
    test_index_boot_splash_is_token_driven()
    test_index_inline_accent_is_theme_aware()
    test_base_css_native_color_scheme()
    test_select_options_follow_scheme_in_light()
    test_legacy_derivation_is_active_theme_aware()
    test_accent_derivation_is_theme_aware()
    test_dark_custom_scheme_round_trip_and_sidecar()
    print("All v8.20 dark-mode tests passed.")
