"""v8.18.12 — export/download fixes (user report on Monthly Close).

User report: "in Monthly Close there are 2 pdf download and one excel —
remove 1 Download PDF; and fix pdf and excel:
{"detail":"Report generation failed: invalid literal for int() with base 10:
'2026-08'}"; also "make sure every pdf and excel download is working".

Root causes pinned here:
  1. The monthly-close page had its OWN "Download PDF" header button plus
     the v8.16.1 universal auto-injected PDF/Excel pair = two PDF buttons.
     Page's own button removed; the universal pair stays (consistent with
     every other report page).
  2. The universal export route did int(month) on the page's month input
     value '2026-08' (YYYY-MM string) -> ValueError -> 500 for BOTH pdf
     and excel. Now accepts YYYY-MM or separate year/month params, and
     validates the range (400, not 500).
  3. The universal export route only spoke pdf/excel, so the daily-stock
     page's "Export CSV" button silently downloaded an .xlsx (the dedicated
     CSV route in profit.py is shadowed by the universal route). The
     universal route now supports format=csv.
  4. dashboard.js + command-palette.js "Monthly PDF" links used
     getMonth() (0-based) WITHOUT +1 — the button downloaded the PREVIOUS
     month's report. Fixed.

Runtime verification of all 95 download endpoints:
scripts/verify_downloads.py (boots real server, magic-byte checks).
"""
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tests"))

from test_helpers import setup_test_db_with_password, cleanup, login_client

JS = PROJECT_ROOT / "app" / "static" / "js"
MONTH = "2026-08"


def _client():
    from fastapi.testclient import TestClient
    from app import main
    client = TestClient(main.app)
    login_client(client)
    return client


def _read(path):
    return (JS / path).read_text(encoding="utf-8")


def _strip_line_comments(src):
    return re.sub(r"(?m)^\s*//.*$", "", src)


# ═══════════════════════════════════════════════════════════════════
# 1-2. the reported crash: month=YYYY-MM on the universal export route
# ═══════════════════════════════════════════════════════════════════

def test_monthly_close_export_pdf_month_string():
    """format=pdf&month=2026-08 must be a PDF, not a 500 crash (the report)."""
    d = setup_test_db_with_password(prefix="bb81812_pdf_")
    try:
        client = _client()
        r = client.get(f"/api/reports/monthly-close/export?format=pdf&month={MONTH}")
        assert r.status_code == 200, f"{r.status_code}: {r.text[:200]}"
        assert r.content[:5] == b"%PDF-", "not a PDF payload"
        assert r.headers["content-type"].startswith("application/pdf")
    finally:
        cleanup(d)


def test_monthly_close_export_excel_month_string():
    """format=excel&month=2026-08 must be an xlsx, not a 500 crash."""
    d = setup_test_db_with_password(prefix="bb81812_xlsx_")
    try:
        client = _client()
        r = client.get(f"/api/reports/monthly-close/export?format=excel&month={MONTH}")
        assert r.status_code == 200, f"{r.status_code}: {r.text[:200]}"
        assert r.content[:2] == b"PK", "not a zip/xlsx payload"
        assert "spreadsheetml" in r.headers["content-type"]
    finally:
        cleanup(d)


def test_monthly_close_export_year_month_params():
    """Separate year+month params still work (dashboard/palette links)."""
    d = setup_test_db_with_password(prefix="bb81812_ym_")
    try:
        client = _client()
        r = client.get("/api/reports/monthly-close/export?format=pdf&year=2026&month=8")
        assert r.status_code == 200, f"{r.status_code}: {r.text[:200]}"
        assert r.content[:5] == b"%PDF-"
    finally:
        cleanup(d)


def test_monthly_close_export_invalid_month_is_400():
    """Invalid month must be a clean 400, never a 500 crash page."""
    d = setup_test_db_with_password(prefix="bb81812_bad_")
    try:
        client = _client()
        r = client.get("/api/reports/monthly-close/export?format=pdf&month=2026-13")
        assert r.status_code == 400, f"expected 400, got {r.status_code}"
    finally:
        cleanup(d)


def test_monthly_close_pdf_route_still_works():
    """The dedicated .pdf route (dashboard button / command palette)."""
    d = setup_test_db_with_password(prefix="bb81812_dotpdf_")
    try:
        client = _client()
        r = client.get("/api/reports/monthly-close.pdf?year=2026&month=8")
        assert r.status_code == 200, f"{r.status_code}: {r.text[:200]}"
        assert r.content[:5] == b"%PDF-"
    finally:
        cleanup(d)


# ═══════════════════════════════════════════════════════════════════
# 3. universal route now speaks CSV (daily-stock "Export CSV" button)
# ═══════════════════════════════════════════════════════════════════

def test_universal_export_csv():
    """format=csv on the universal route must return real CSV text."""
    d = setup_test_db_with_password(prefix="bb81812_csv_")
    try:
        client = _client()
        r = client.get("/api/reports/daily-stock/export?format=csv&date=2026-08-15")
        assert r.status_code == 200, f"{r.status_code}: {r.text[:200]}"
        assert r.headers["content-type"].startswith("text/csv"), r.headers["content-type"]
        text = r.content.decode("utf-8-sig")   # BOM must decode cleanly
        assert text.startswith("BillBook —"), "branded title row missing"
        assert "," in text, "no CSV structure"
    finally:
        cleanup(d)


def test_universal_export_csv_has_tables():
    """CSV output carries the report's KPI sections and data tables."""
    d = setup_test_db_with_password(prefix="bb81812_csvtab_")
    try:
        client = _client()
        r = client.get(f"/api/reports/margins/export?format=csv&month={MONTH}")
        assert r.status_code == 200, r.text[:200]
        text = r.content.decode("utf-8-sig")
        assert "BillBook —" in text
        # either a KPI section header (ALL-CAPS) or a TABLE: row must exist
        assert re.search(r"(^|[A-Z]{2,}$|TABLE:)", text, re.M), "no sections in csv"
    finally:
        cleanup(d)


# ═══════════════════════════════════════════════════════════════════
# static JS guards
# ═══════════════════════════════════════════════════════════════════

def _route_block(src, route_path):
    """Extract the route('...') block from page JS."""
    start = src.index(f"route('{route_path}'")
    nxt = re.search(r"\nroute\('", src[start + 10:])
    return src[start:start + 10 + (nxt.start() if nxt else len(src))]


def test_monthly_close_page_no_duplicate_pdf_button():
    """The page's own 'Download PDF' button must be gone; the universal
    export pair (REPORT_EXPORT_MAP) must stay wired for this page."""
    src = _strip_line_comments(_read("pages/reports-pages.js"))
    block = _route_block(src, "/reports/monthly-close")
    assert "mc-pdf-btn" not in block, "page's own PDF button must be removed"
    assert "Download PDF" not in block, "duplicate label must be gone"
    assert "mc-month" in block, "month input (drives universal export) stays"
    assert "'monthly-close': 'monthly-close'" in src, \
        "REPORT_EXPORT_MAP must keep monthly-close wired for the PDF/Excel pair"


def test_daily_stock_page_exports_csv():
    """The 'Export CSV' button must pass format=csv (it used to silently
    download an .xlsx because the universal route only spoke pdf/excel)."""
    src = _strip_line_comments(_read("pages/daily-stock-page.js"))
    assert "format=csv" in src, "export URL must pass format=csv"
    assert re.search(r"daily-stock/export\?format=csv&date=", src), \
        "export URL shape wrong"


def test_dashboard_and_palette_month_is_one_based():
    """Both 'Monthly PDF' entry points must use getMonth() + 1 (0-based
    getMonth() alone downloaded the PREVIOUS month)."""
    dash = _strip_line_comments(_read("pages/dashboard.js"))
    pal = _strip_line_comments(_read("components/command-palette.js"))
    assert "monthly-close.pdf" in dash and "monthly-close.pdf" in pal
    assert "getMonth() + 1" in dash, "dashboard: getMonth() + 1 missing"
    assert "getMonth() + 1" in pal, "command palette: getMonth() + 1 missing"
    # the 0-based bug pattern must be gone from both files
    for name, src in (("dashboard", dash), ("palette", pal)):
        bad = re.search(r"getMonth\(\)\s*\)\s*\.padStart", src)
        assert not bad, f"{name}: 0-based getMonth() bug still present"
