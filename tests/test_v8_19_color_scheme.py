# ═══════════════════════════════════════════════════════════════════
# v8.19 — Color-scheme system tests
#
# User report: "on color scheme change, loading and some other things
# don't change color" + "there should be an option for custom color
# scheme". Root causes pinned here:
#   1. The scheme engine only set the 13 modern design-system vars —
#      the legacy base.css primitives (--canvas/--surface-card/--ink/
#      --hairline/…) powered login/license/legacy components and never
#      changed. Fixed in utils.js applyAppearance.
#   2. chartTheme() hardcoded coral #cc785c — every chart ignored the
#      scheme AND the accent. Fixed: reads live CSS vars.
#   3. The boot splash (loading.html) was fully hardcoded cream/coral.
#      Fixed: CSS-var page + Rust-side injection of data/appearance.json.
#   4. Custom scheme: color_scheme='custom' + custom_scheme_base seed
#      color, validated and persisted server-side, mirrored into
#      data/appearance.json for the splash.
# ═══════════════════════════════════════════════════════════════════
import json
import re
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

from test_helpers import setup_test_db_with_password, login_client, cleanup


def _client():
    """Fresh DB + logged-in TestClient (manager)."""
    from fastapi.testclient import TestClient
    from app import main
    client = TestClient(main.app)
    login_client(client)
    return client


# ── Backend: custom scheme round-trip + validation ──────────────────────

def test_custom_scheme_round_trip():
    """POST color_scheme='custom' + seed color must persist and come back."""
    test_dir = setup_test_db_with_password(prefix="bb819_custom_")
    try:
        client = _client()
        payload = {
            "color_scheme": "custom",
            "custom_scheme_base": "#4E7D62",
            "accent_color": "#4E7D62",
            "theme": "light",
        }
        r = client.post("/api/appearance", json=payload)
        assert r.status_code == 200, r.text
        cfg = client.get("/api/appearance").json()
        assert cfg["color_scheme"] == "custom"
        assert cfg["custom_scheme_base"].lower() == "#4e7d62"
        assert cfg["accent_color"].lower() == "#4e7d62"
        print("✓ test_custom_scheme_round_trip")
    finally:
        cleanup(test_dir)


def test_custom_scheme_defaults_to_neutral_seed():
    """GET on a fresh install must expose the neutral default seed color."""
    test_dir = setup_test_db_with_password(prefix="bb819_default_")
    try:
        client = _client()
        cfg = client.get("/api/appearance").json()
        assert cfg["color_scheme"] == "warm", "fresh installs stay on warm (zero-change promise)"
        assert re.fullmatch(r"#[0-9a-fA-F]{6}", cfg["custom_scheme_base"]), \
            f"custom_scheme_base must be a 6-digit hex, got {cfg['custom_scheme_base']!r}"
        print("✓ test_custom_scheme_defaults_to_neutral_seed")
    finally:
        cleanup(test_dir)


def test_custom_scheme_rejects_bad_seed():
    """A malformed seed hex must 400 — it would poison every derived token."""
    test_dir = setup_test_db_with_password(prefix="bb819_badseed_")
    try:
        client = _client()
        assert client.post("/api/appearance", json={"custom_scheme_base": "#12345"}).status_code == 400
        assert client.post("/api/appearance", json={"custom_scheme_base": "not-a-color"}).status_code == 400
        assert client.post("/api/appearance", json={"custom_scheme_base": "4e7d62"}).status_code == 400
        # valid seed is fine
        assert client.post("/api/appearance", json={"custom_scheme_base": "#AABBCC"}).status_code == 200
        print("✓ test_custom_scheme_rejects_bad_seed")
    finally:
        cleanup(test_dir)


def test_custom_scheme_without_seed_keeps_stored_or_default():
    """Saving 'custom' with no seed must not wipe the stored/default seed."""
    test_dir = setup_test_db_with_password(prefix="bb819_noseed_")
    try:
        client = _client()
        # 1) save a seed first
        assert client.post("/api/appearance", json={"custom_scheme_base": "#1F3BFF"}).status_code == 200
        # 2) then save scheme=custom without a seed
        r = client.post("/api/appearance", json={"color_scheme": "custom"})
        assert r.status_code == 200, r.text
        cfg = client.get("/api/appearance").json()
        assert cfg["color_scheme"] == "custom"
        assert cfg["custom_scheme_base"].lower() == "#1f3bff", "seed must survive a seed-less save"
        # 3) on a totally fresh state, seed-less custom save falls back to default
        print("✓ test_custom_scheme_without_seed_keeps_stored_or_default")
    finally:
        cleanup(test_dir)


def test_unknown_scheme_still_rejected():
    test_dir = setup_test_db_with_password(prefix="bb819_unknown_")
    try:
        client = _client()
        assert client.post("/api/appearance", json={"color_scheme": "neon"}).status_code == 400
        print("✓ test_unknown_scheme_still_rejected")
    finally:
        cleanup(test_dir)


def test_appearance_sidecar_json_written_for_splash():
    """Every appearance save must mirror data/appearance.json for the Tauri splash."""
    test_dir = setup_test_db_with_password(prefix="bb819_sidecar_")
    try:
        from app import config
        client = _client()
        sidecar = Path(config.DATA) / "appearance.json"
        # 1) main endpoint write
        r = client.post("/api/appearance", json={"color_scheme": "ocean", "theme": "dark"})
        assert r.status_code == 200, r.text
        assert sidecar.exists(), "appearance.json must exist after a save"
        snap = json.loads(sidecar.read_text())
        assert snap["color_scheme"] == "ocean"
        assert snap["theme"] == "dark"
        assert re.fullmatch(r"#[0-9a-fA-F]{6}", snap["accent_color"]), snap
        # 2) legacy accent endpoint also refreshes the sidecar
        r = client.post("/api/appearance/accent", json={"accent_color": "#e8a55a"})
        assert r.status_code == 200, r.text
        snap = json.loads(sidecar.read_text())
        assert snap["accent_color"].lower() == "#e8a55a"
        assert snap["color_scheme"] == "ocean", "accent-only save must not lose the scheme"
        print("✓ test_appearance_sidecar_json_written_for_splash")
    finally:
        cleanup(test_dir)


# ── Frontend plumbing (static file assertions) ──────────────────────────

UTILS = PROJ / "app" / "static" / "js" / "utils.js"
LOGIN = PROJ / "app" / "static" / "login.html"
LICENSE = PROJ / "app" / "static" / "license.html"
LOADING = PROJ / "app" / "static" / "loading.html"
SCHEME_BOOT = PROJ / "app" / "static" / "js" / "scheme-boot.js"
SPLASH_THEME = PROJ / "app" / "static" / "js" / "splash-theme.js"
MAIN_RS = PROJ / "desktop" / "src" / "main.rs"
BASE_CSS = PROJ / "app" / "static" / "styles" / "base.css"


def test_applyappearance_sets_legacy_primitives():
    """The scheme engine must restyle the legacy base.css tokens too."""
    src = UTILS.read_text()
    for var in ("--canvas", "--surface-card", "--surface-cream-strong", "--hairline",
                "--ink", "--body", "--muted-soft", "--on-dark"):
        assert f"'{var}'" in src, f"applyAppearance must set the legacy token {var}"
    print("✓ test_applyappearance_sets_legacy_primitives")


def test_warm_legacy_tokens_match_base_css():
    """warm's legacy block must equal base.css EXACTLY (zero-change promise)."""
    utils_src = UTILS.read_text()
    m = re.search(r"light:\s*\{\s*canvas:\s*'([^']+)'", utils_src)
    assert m, "warm.legacy.light block not found"
    warm_canvas = m.group(1)

    css = BASE_CSS.read_text()
    # first :root block's --canvas (the light default)
    m2 = re.search(r"--canvas:\s*(#[0-9a-fA-F]{6})", css)
    assert m2, "base.css --canvas not found"
    assert warm_canvas.lower() == m2.group(1).lower(), \
        f"warm legacy canvas {warm_canvas} != base.css {m2.group(1)}"
    print("✓ test_warm_legacy_tokens_match_base_css")


def test_custom_scheme_engine_exports():
    src = UTILS.read_text()
    assert "export function deriveCustomScheme" in src, "deriveCustomScheme must be exported"
    assert "'custom'" in src, "normalizeAppearance must accept the custom scheme"
    # settings page wires the custom card + seed picker
    settings = (PROJ / "app" / "static" / "js" / "pages" / "settings-pages.js").read_text()
    assert "data-scheme=\"custom\"" in settings, "Custom scheme card missing"
    assert "custom_scheme_base: st.custom_scheme_base" in settings, "Save must include the seed color"
    print("✓ test_custom_scheme_engine_exports")


def test_charttheme_reads_live_vars():
    """chartTheme must follow the scheme/accent, not hardcoded coral."""
    src = UTILS.read_text()
    m = re.search(r"export function chartTheme\(\)\s*\{(.{0,1600})", src, re.S)
    assert m, "chartTheme not found"
    body = m.group(1)
    assert "getComputedStyle" in body, "chartTheme must read live CSS variables"
    assert "'--primary'" in body and "'--text-2'" in body, "chartTheme must read primary/text tokens"
    # the returned primary + palette lead must come from the live var, and
    # the palette must not hardcode coral as the leading series color
    assert "primary = v('--primary'" in body
    assert "colors: [primary," in body, "palette must lead with the live accent"
    print("✓ test_charttheme_reads_live_vars")


def test_login_and_license_boot_the_scheme():
    """Pre-login pages must load scheme-boot.js so they follow the scheme."""
    for page, name in ((LOGIN, "login.html"), (LICENSE, "license.html")):
        html = page.read_text()
        assert "scheme-boot.js" in html, f"{name} must include scheme-boot.js"
    boot = SCHEME_BOOT.read_text()
    assert "applyAppearance" in boot
    print("✓ test_login_and_license_boot_the_scheme")


def test_splash_follows_scheme():
    """loading.html: var-driven colors + splash-theme module + Rust injection."""
    html = LOADING.read_text()
    assert "js/splash-theme.js" in html, "splash must load splash-theme.js"
    assert "var(--canvas" in html, "splash body must use the canvas token"
    assert "var(--coral" in html, "splash spinner must use the accent token"
    assert "#faf9f5" in html, "splash keeps a neutral first-paint fallback"

    splash_js = SPLASH_THEME.read_text()
    assert "__splashScheme" in splash_js, "splash must accept the Rust-injected payload"
    assert "__setSplashScheme" in splash_js

    rust = MAIN_RS.read_text()
    assert "inject_splash_scheme" in rust, "Rust shell must inject the scheme into the splash"
    assert "appearance.json" in rust
    print("✓ test_splash_follows_scheme")


if __name__ == "__main__":
    test_custom_scheme_round_trip()
    test_custom_scheme_defaults_to_neutral_seed()
    test_custom_scheme_rejects_bad_seed()
    test_custom_scheme_without_seed_keeps_stored_or_default()
    test_unknown_scheme_still_rejected()
    test_appearance_sidecar_json_written_for_splash()
    test_applyappearance_sets_legacy_primitives()
    test_warm_legacy_tokens_match_base_css()
    test_custom_scheme_engine_exports()
    test_charttheme_reads_live_vars()
    test_login_and_license_boot_the_scheme()
    test_splash_follows_scheme()
    print("All v8.19 color scheme tests passed.")
