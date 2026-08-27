# ═══════════════════════════════════════════════════════════════════
# v8.15.0 — Branding / Appearance system tests (design.md)
#
# The pre-v8.15 appearance settings were dead controls: accent color,
# density and font scale were saved to the DB but NOTHING applied them.
# These tests pin the fixed behavior:
#   1. /api/appearance defaults = design.md brand defaults (coral etc.)
#   2. Round-trip of all 6 appearance fields
#   3. Server-side validation (bad accent hex, bad radius, bad font scale)
#   4. The frontend plumbing exists (utils.js applyAppearance engine,
#      index.html pre-paint boot, base.css data-density/serif hooks)
#   5. The settings page exposes every design.md option
# ═══════════════════════════════════════════════════════════════════
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


# ── Backend: defaults, round-trip, validation ────────────────────────────

def test_appearance_defaults_match_design_md():
    """GET /api/appearance on a fresh install must return the design.md brand defaults."""
    test_dir = setup_test_db_with_password(prefix="bb815_defaults_")
    try:
        client = _client()
        r = client.get("/api/appearance")
        assert r.status_code == 200, r.text
        cfg = r.json()
        # design.md: cream canvas (light) + coral #cc785c + serif display + standard radius
        assert cfg["theme"] == "light", "design.md default theme is the cream canvas (light)"
        assert cfg["accent_color"].lower() == "#cc785c", f"expected coral, got {cfg['accent_color']}"
        assert cfg["serif_headings"] is True
        assert cfg["radius"] == "standard"
        assert cfg["density"] == "comfortable"
        assert cfg["font_scale"] == "100"
        print("✓ test_appearance_defaults_match_design_md")
    finally:
        cleanup(test_dir)


def test_appearance_round_trip_all_design_md_fields():
    """POST then GET must persist every design.md option."""
    test_dir = setup_test_db_with_password(prefix="bb815_roundtrip_")
    try:
        client = _client()
        payload = {
            "theme": "dark",
            "accent_color": "#5db8a6",   # design.md accent-teal
            "density": "compact",
            "font_scale": "110",
            "serif_headings": False,
            "radius": "roomy",
        }
        r = client.post("/api/appearance", json=payload)
        assert r.status_code == 200, r.text
        cfg = client.get("/api/appearance").json()
        assert cfg["theme"] == "dark"
        assert cfg["accent_color"].lower() == "#5db8a6"
        assert cfg["density"] == "compact"
        assert cfg["font_scale"] == "110"
        assert cfg["serif_headings"] is False
        assert cfg["radius"] == "roomy"
        print("✓ test_appearance_round_trip_all_design_md_fields")
    finally:
        cleanup(test_dir)


def test_appearance_rejects_invalid_accent():
    """A malformed hex must be rejected — it would poison every CSS variable client-side."""
    test_dir = setup_test_db_with_password(prefix="bb815_accent_")
    try:
        client = _client()
        assert client.post("/api/appearance", json={"accent_color": "not-a-color"}).status_code == 400
        assert client.post("/api/appearance", json={"accent_color": "#12345"}).status_code == 400
        assert client.post("/api/appearance", json={"accent_color": "cc785c"}).status_code == 400
        # valid 6-digit still fine
        assert client.post("/api/appearance", json={"accent_color": "#AABBCC"}).status_code == 200
        print("✓ test_appearance_rejects_invalid_accent")
    finally:
        cleanup(test_dir)


def test_appearance_rejects_invalid_radius_density_scale():
    test_dir = setup_test_db_with_password(prefix="bb815_invalid_")
    try:
        client = _client()
        assert client.post("/api/appearance", json={"radius": "extra-roundy"}).status_code == 400
        assert client.post("/api/appearance", json={"density": "cozy"}).status_code == 400
        assert client.post("/api/appearance", json={"font_scale": "130"}).status_code == 400
        assert client.post("/api/appearance", json={"font_scale": "50"}).status_code == 400
        assert client.post("/api/appearance", json={"font_scale": "abc"}).status_code == 400
        # boundaries are accepted
        assert client.post("/api/appearance", json={"font_scale": "90"}).status_code == 200
        assert client.post("/api/appearance", json={"font_scale": "120"}).status_code == 200
        print("✓ test_appearance_rejects_invalid_radius_density_scale")
    finally:
        cleanup(test_dir)


def test_old_accent_alias_endpoint_still_works():
    """Legacy POST /api/appearance/accent (SnowUI-era) must keep functioning."""
    test_dir = setup_test_db_with_password(prefix="bb815_alias_")
    try:
        client = _client()
        r = client.post("/api/appearance/accent", json={"accent_color": "#e8a55a"})
        assert r.status_code == 200, r.text
        assert client.get("/api/appearance").json()["accent_color"].lower() == "#e8a55a"
        print("✓ test_old_accent_alias_endpoint_still_works")
    finally:
        cleanup(test_dir)


def test_shop_profile_round_trip_from_branding_page():
    """The Shop Branding card persists via /api/shop-profile (no frontend existed before)."""
    test_dir = setup_test_db_with_password(prefix="bb815_shop_")
    try:
        client = _client()
        r = client.post("/api/shop-profile", json={
            "shop_name": "Al-Madina Kiryana",
            "phone": "0300-1234567",
            "address": "Shop 12, Main Bazaar, Lahore",
            "ntn": "1234567",
            "strn": "7654321",
            "logo": "/static/icons/icon-192.png",
            "receipt_footer": "Shukriya! Aap dobara tashreef laayen.",
        })
        assert r.status_code == 200, r.text
        p = client.get("/api/shop-profile").json()
        assert p["shop_name"] == "Al-Madina Kiryana"
        assert p["ntn"] == "1234567"
        assert p["receipt_footer"].startswith("Shukriya")
        print("✓ test_shop_profile_round_trip_from_branding_page")
    finally:
        cleanup(test_dir)


# ── Frontend plumbing: the engine that was missing before v8.15 ──────────

def test_frontend_appearance_engine_exists():
    """utils.js must export the applyAppearance engine — the missing piece that
    made accent/density/font-scale dead controls before v8.15."""
    src = (PROJ / "app" / "static" / "js" / "utils.js").read_text()
    for fn in ("applyAppearance", "initAppearance", "cacheAppearance", "normalizeAppearance"):
        assert f"export function {fn}" in src or f"export async function {fn}" in src, f"utils.js missing {fn}"
    # accent must drive the CSS token family (not just be stored)
    assert "'--coral'" in src and "'--primary'" in src and "'--accent'" in src
    assert "'--radius-lg'" in src
    # server sync: engine must pull /api/appearance at boot
    assert "fetch('/api/appearance'" in src
    print("✓ test_frontend_appearance_engine_exists")


def test_index_html_prepaint_boot_applies_full_appearance():
    """The pre-paint inline script must apply cached appearance (theme + accent +
    density + serif + radius + zoom) — not just data-theme like v8.14."""
    html = (PROJ / "app" / "static" / "index.html").read_text()
    assert "bb-appearance" in html, "boot must read the cached appearance object"
    for token in ("--coral", "--radius-lg", "data-density", "data-serif", "zoom"):
        assert token in html, f"pre-paint boot missing {token}"
    # design.md default: cream canvas, not the old dark default
    assert "accent_color: '#cc785c'" in html
    assert "theme: 'light'" in html
    print("✓ test_index_html_prepaint_boot_applies_full_appearance")


def test_base_css_has_density_and_serif_hooks():
    """base.css must implement the data-density and data-serif switches."""
    css = (PROJ / "app" / "static" / "styles" / "base.css").read_text()
    assert '[data-density="compact"]' in css, "compact density rules missing"
    assert '[data-serif="off"]' in css, "serif-off typography rules missing"
    print("✓ test_base_css_has_density_and_serif_hooks")


def test_app_js_boots_appearance_sync():
    """app.js must call initAppearance() so settings follow the account across devices."""
    src = (PROJ / "app" / "static" / "js" / "app.js").read_text()
    assert "initAppearance" in src
    print("✓ test_app_js_boots_appearance_sync")


def test_settings_page_exposes_every_design_md_option():
    """The Appearance page must contain every design.md option (v8.14 had only 4)."""
    src = (PROJ / "app" / "static" / "js" / "pages" / "settings-pages.js").read_text()
    # the six appearance controls
    assert "ap-serif-seg" in src, "serif display toggle missing"
    assert "ap-radius-card" in src, "radius scale cards missing"
    assert "ap-swatch" in src, "accent presets missing"
    assert "ap-density-seg" in src, "density control missing"
    assert "ap-font-scale" in src, "font scale slider missing"
    assert "appearance-theme-card" in src, "theme cards missing"
    # live apply (the old page only applied theme on SAVE)
    assert "applyAppearance(st)" in src, "changes must preview live"
    # shop branding (endpoints existed since v8.9 but had NO frontend UI)
    assert "apiPost('/api/shop-profile'" in src, "shop branding save missing"
    assert "apiPost('/api/receipt-template'" in src, "receipt template save missing"
    # reset to design.md defaults
    assert "ap-reset-btn" in src
    print("✓ test_settings_page_exposes_every_design_md_option")


def test_appearance_and_branding_css_components_exist():
    """shell.css must style the new controls and use design.md preview colors."""
    css = (PROJ / "app" / "static" / "css" / "shell.css").read_text()
    for cls in (".ap-swatch", ".ap-seg", ".ap-radius-card", ".ap-preview"):
        assert cls in css, f"{cls} styles missing"
    # theme previews must use design.md surfaces, not the old Linear blue-gray
    assert "#faf9f5" in css and "#181715" in css
    print("✓ test_appearance_and_branding_css_components_exist")


if __name__ == "__main__":
    import traceback
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception:
            failed += 1
            print(f"  FAIL {t.__name__}")
            traceback.print_exc()
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
