#!/usr/bin/env python3
# ═══════════════════════════════════════════════════════════════════
# DOWNLOAD VERIFIER (v8.18.12) — "make sure every pdf and excel
# download is working"
#
#   1. Boots the REAL backend (uvicorn + license-bypass E2E wrapper,
#      throttle neutralized) on a temp DB seeded with sample data.
#   2. Logs in (POST /api/login, session cookie).
#   3. Hits EVERY download endpoint in the system:
#        - the universal /api/reports/{name}/export (pdf + excel)
#          for ALL 30 report names in the backend map
#        - every DEDICATED export route (monthly-close.pdf, billwise,
#          profit-analysis, sold-stock, daily-stock, bills.xlsx,
#          insights.xlsx, export.csv, fbr, fbr.csv, activity,
#          accountant zip)
#   4. Per download asserts: HTTP 200, expected Content-Type family,
#      MAGIC BYTES (%PDF / PK.. zip), and a minimum plausible size.
#
# No production file is touched. Run from the repo root:
#   python scripts/verify_downloads.py
# Results JSON: scripts/verify_downloads_results.json
# ═══════════════════════════════════════════════════════════════════
import http.cookiejar
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]   # repo root
PORT = 8823
BASE = f"http://127.0.0.1:{PORT}"
OUT_JSON = Path(__file__).resolve().parent / 'verify_downloads_results.json'
PASSWORD = "testpass"
MONTH = "2026-08"            # seeded sample data lives in Aug 2026
START, END = "2026-06-01", "2026-08-31"
DATE = "2026-08-15"


def _pkill(pattern: str):
    if os.name == "posix":
        subprocess.run(["pkill", "-9", "-f", pattern], capture_output=True)


# ── 1. temp DB + sample data (same proven E2E seeding as the sweep) ──
data_dir = tempfile.mkdtemp(prefix="bb_dl_")
os.environ["BILLBOOK_DATA_DIR"] = data_dir
sys.path.insert(0, str(PROJ))
from app import db
from app.security import hash_password

db.init()
SAMPLE_SQL = PROJ / "tests" / "sample_data.sql"
with db.conn() as c:
    existing = {r[0] for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    for t in ("sale_items", "sales", "bill_items", "bills", "customers",
              "price_categories", "suppliers", "stock_adjustments",
              "activity_log", "expenses", "reorder_reminders",
              "expense_categories", "cash_drawer", "employees",
              "automation_config", "category_stock_state",
              "owner_withdrawals", "lost_sales", "closed_days", "seasons",
              "ai_usage", "pending_actions", "branches", "price_rules",
              "bundles", "bundle_items", "transfer_challans",
              "central_purchases", "price_pushes", "audit_runs",
              "bill_intelligence", "ezi_pos_imports", "pos_expense_imports"):
        if t in existing:
            c.execute(f"DELETE FROM {t}")
    with open(SAMPLE_SQL) as f:
        c.executescript(f.read())
    defaults = [("Rent", 1, 50000, 1, 0), ("Salaries", 1, 80000, 2, 0),
                ("Electricity", 0, 0, 3, 0), ("Transport", 0, 0, 4, 0),
                ("Internet", 0, 0, 5, 0), ("Maintenance", 0, 0, 6, 0),
                ("Marketing", 0, 0, 7, 0), ("Other", 0, 0, 8, 0)]
    for name, is_fixed, budget, sort_order, _ in defaults:
        c.execute("INSERT INTO expense_categories(name,is_fixed,budget_monthly,active,sort_order)"
                  " VALUES(?,?,?,1,?)", (name, is_fixed, budget, sort_order))
    c.execute("INSERT INTO cash_drawer(type, amount, description) VALUES('opening', 50000, 'dl-check')")
    c.execute("INSERT INTO employees(name, role, pin, active) VALUES('DL Mgr', 'manager', '1234', 1)")
    c.execute("INSERT INTO purchase_orders(id, po_no, supplier_id, supplier_name, status, "
              "total, notes, expected_date) VALUES(1, 'PO-0001', 1, 'E2E Supplies', 'sent', "
              "950, 'dl seed', '2026-09-10')")
    c.execute("INSERT INTO purchase_order_items(po_id, item_name, qty, est_price, line_total) "
              "VALUES(1, 'E2E Widget', 10, 95, 950)")
    c.execute("DELETE FROM settings WHERE key IN ('password_hash','setup_completed','start_page')")
    c.execute("INSERT INTO settings(key, value) VALUES(?,?)",
              ("password_hash", hash_password(PASSWORD)))
    c.execute("INSERT INTO settings(key, value) VALUES('setup_completed','true')")
    c.execute("INSERT INTO settings(key, value) VALUES('start_page','launcher')")
print(f"[setup] DB at {data_dir}")

# ── 2. license-bypass + throttle-bypass wrapper, boot uvicorn ────────
_pkill(f"uvicorn.*--port {PORT}")
time.sleep(0.5)
wrapper = Path(data_dir) / "e2e_wrapper.py"
wrapper.write_text(
    "# E2E ONLY — never shipped. License gate + API throttle bypassed\n"
    "# (the download verifier makes ~90 requests).\n"
    "import app.licensing as _lic\n"
    "_lic.is_activated = lambda: True\n"
    "_lic.license_state = lambda: {'required': True, 'activated': True, "
    "'setup_id': 'E2E', 'license': None, 'reason': None}\n"
    "async def _no_throttle(self, request, call_next):\n"
    "    return await call_next(request)\n"
    "from app.main import app, APIThrottleMiddleware\n"
    "APIThrottleMiddleware.dispatch = _no_throttle\n"
    "app = app\n"
)
server = subprocess.Popen(
    [sys.executable, "-m", "uvicorn", "e2e_wrapper:app",
     "--app-dir", str(data_dir), "--port", str(PORT), "--log-level", "warning"],
    cwd=PROJ, env={**os.environ, "PYTHONPATH": f"{PROJ}{os.pathsep}{data_dir}"},
    stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
)
_ready = False
for _ in range(90):
    try:
        urllib.request.urlopen(f"{BASE}/login", timeout=1)
        _ready = True
        break
    except Exception:
        time.sleep(0.5)
assert _ready, "server never became ready"
assert server.poll() is None, "uvicorn child died"
print(f"[setup] server on :{PORT}")

# ── 3. login (session cookie) ────────────────────────────────────────
jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
login = urllib.request.Request(
    f"{BASE}/api/login",
    data=json.dumps({"password": PASSWORD}).encode(),
    headers={"Content-Type": "application/json"}, method="POST")
resp = json.loads(opener.open(login, timeout=10).read())
assert resp.get("ok"), f"login failed: {resp}"
print("[login] ok")

# ── 4. every download endpoint in the system ─────────────────────────
# (label, path, expected kind) kind ∈ pdf|xlsx|csv|json|zip
CHECKS = [
    # dedicated routes
    ("monthly-close.pdf year+month", "/api/reports/monthly-close.pdf?year=2026&month=8", "pdf"),
    ("export bills.xlsx", "/api/export/bills.xlsx", "xlsx"),
    ("export insights.xlsx", "/api/export/insights.xlsx", "xlsx"),
    ("export.csv legacy", "/api/export.csv", "csv"),
    ("export fbr (json)", f"/api/export/fbr?start={START}&end={END}", "json"),
    ("export fbr.csv", f"/api/export/fbr.csv?start={START}&end={END}", "csv"),
    ("activity export", f"/api/activity/export?start={START}&end={END}", "csv"),
    ("accountant export zip", f"/api/export/accountant?month={MONTH}", "zip"),
    ("billwise xlsx (default)", f"/api/reports/billwise/export?start={START}&end={END}&status=all", "xlsx"),
    ("billwise pdf", f"/api/reports/billwise/export?format=pdf&start={START}&end={END}&status=all", "pdf"),
    ("billwise xlsx bill_ids", f"/api/reports/billwise/export?start={START}&end={END}&status=all&bill_ids=1", "xlsx"),
    ("profit-analysis csv (default)", f"/api/reports/profit-analysis/export?start={START}&end={END}&group_by=category", "csv"),
    ("profit-analysis csv by month", f"/api/reports/profit-analysis/export?start={START}&end={END}&group_by=month", "csv"),
    ("profit-analysis pdf", f"/api/reports/profit-analysis/export?format=pdf&start={START}&end={END}&group_by=category", "pdf"),
    ("profit-analysis excel", f"/api/reports/profit-analysis/export?format=excel&start={START}&end={END}&group_by=category", "xlsx"),
    ("sold-stock csv (default)", f"/api/reports/sold-stock/export?start={START}&end={END}&group_by=category", "csv"),
    ("sold-stock pdf", f"/api/reports/sold-stock/export?format=pdf&start={START}&end={END}&group_by=category", "pdf"),
    ("sold-stock excel", f"/api/reports/sold-stock/export?format=excel&start={START}&end={END}&group_by=category", "xlsx"),
    ("daily-stock export csv", f"/api/reports/daily-stock/export?format=csv&date={DATE}", "csv"),
    ("daily-stock export bare (excel default)", f"/api/reports/daily-stock/export?date={DATE}", "xlsx"),
    # universal route — the v8.18.12 reported bug (month=YYYY-MM)
    ("monthly-close pdf month=YYYY-MM", f"/api/reports/monthly-close/export?format=pdf&month={MONTH}", "pdf"),
    ("monthly-close excel month=YYYY-MM", f"/api/reports/monthly-close/export?format=excel&month={MONTH}", "xlsx"),
    ("monthly-close pdf year+month", f"/api/reports/monthly-close/export?format=pdf&year=2026&month=8", "pdf"),
]

# universal route: ALL report names from the backend report_map, pdf + excel.
# Param groups mirror the export route's dispatch logic.
UNIV_MONTH = ["monthly", "ytd", "margins", "earnings", "actual-earnings",
              "pnl", "cash-flow", "expenses", "store-profit", "balance-sheet"]
UNIV_DATE = ["cash-buckets", "daily-stock"]
UNIV_RANGE = ["profit-analysis", "sold-stock", "top-items", "peak-hours",
              "shrinkage", "sales-by-customer", "sales-by-employee",
              "atv-basket", "supplier-performance"]
UNIV_TARGETS = ["targets"]
UNIV_BARE = ["overview", "billwise", "audit", "suspicious", "yoy-compare",
             "supplier-comparison", "category-cost-trends",
             "stock-writeoffs", "retention", "gmroi", "sell-through",
             "inventory-turnover", "ar-aging", "ap-aging"]
for name in UNIV_MONTH:
    CHECKS.append((f"univ {name} pdf", f"/api/reports/{name}/export?format=pdf&month={MONTH}", "pdf"))
    CHECKS.append((f"univ {name} excel", f"/api/reports/{name}/export?format=excel&month={MONTH}", "xlsx"))
for name in UNIV_DATE:
    CHECKS.append((f"univ {name} pdf", f"/api/reports/{name}/export?format=pdf&date={DATE}", "pdf"))
    CHECKS.append((f"univ {name} excel", f"/api/reports/{name}/export?format=excel&date={DATE}", "xlsx"))
for name in UNIV_RANGE:
    CHECKS.append((f"univ {name} pdf", f"/api/reports/{name}/export?format=pdf&start={START}&end={END}", "pdf"))
    CHECKS.append((f"univ {name} excel", f"/api/reports/{name}/export?format=excel&start={START}&end={END}", "xlsx"))
for name in UNIV_TARGETS:
    CHECKS.append((f"univ {name} pdf", f"/api/reports/{name}/export?format=pdf&period=daily&target_date={DATE}", "pdf"))
    CHECKS.append((f"univ {name} excel", f"/api/reports/{name}/export?format=excel&period=daily&target_date={DATE}", "xlsx"))
for name in UNIV_BARE:
    CHECKS.append((f"univ {name} pdf", f"/api/reports/{name}/export?format=pdf", "pdf"))
    CHECKS.append((f"univ {name} excel", f"/api/reports/{name}/export?format=excel", "xlsx"))

MAGIC = {"pdf": b"%PDF", "xlsx": b"PK", "zip": b"PK"}
CT_FAMILY = {
    "pdf": ("application/pdf",),
    "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",),
    "csv": ("text/csv",),
    "json": ("application/json",),
    "zip": ("application/zip", "application/x-zip-compressed", "application/octet-stream"),
}
MIN_SIZE = {"pdf": 1000, "xlsx": 3000, "zip": 400, "csv": 50, "json": 2}

results, failures = [], []
for label, path, kind in CHECKS:
    rec = {"label": label, "url": path, "expect": kind}
    try:
        r = opener.open(f"{BASE}{path}", timeout=30)
        body = r.read()
        ct = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
        rec["status"] = r.status
        rec["content_type"] = ct
        rec["size"] = len(body)
        problems = []
        if r.status != 200:
            problems.append(f"HTTP {r.status}")
        if ct not in CT_FAMILY[kind]:
            problems.append(f"content-type {ct!r} != {CT_FAMILY[kind][0]!r}")
        magic = MAGIC.get(kind)
        if magic and not body.startswith(magic):
            problems.append(f"magic bytes {body[:4]!r} != {magic!r}")
        if kind == "csv":
            try:
                body.decode("utf-8-sig")
            except UnicodeDecodeError:
                problems.append("not utf-8 decodable")
            if b"," not in body[:4000]:
                problems.append("no comma in first 4KB")
        if kind == "json":
            try:
                j = json.loads(body)
                rec["json_keys"] = sorted(j.keys())[:8] if isinstance(j, dict) else f"list[{len(j)}]"
            except Exception:
                problems.append("not valid JSON")
        if len(body) < MIN_SIZE[kind]:
            problems.append(f"size {len(body)} < {MIN_SIZE[kind]}")
        rec["ok"] = not problems
        rec["problems"] = problems
        if problems:
            snippet = body[:200].decode("utf-8", "replace")
            rec["body_head"] = snippet
    except urllib.error.HTTPError as e:
        rec["status"] = e.code
        rec["ok"] = False
        rec["problems"] = [f"HTTP {e.code}"]
        rec["body_head"] = e.read()[:300].decode("utf-8", "replace")
    except Exception as e:
        rec["ok"] = False
        rec["problems"] = [f"{type(e).__name__}: {str(e)[:120]}"]
    results.append(rec)
    if not rec["ok"]:
        failures.append(rec)
    mark = "." if rec["ok"] else "!"
    print(f"  [{mark}] {label:44s} {rec.get('status', '?'):>3} {rec.get('size', 0):>9}B "
          + (" ".join(rec["problems"]) if not rec["ok"] else ""))

# ── 5. teardown + report ─────────────────────────────────────────────
server.terminate()
try:
    server.wait(timeout=5)
except Exception:
    server.kill()
_pkill(f"uvicorn.*--port {PORT}")

report = {
    "checked": len(results),
    "ok": len(results) - len(failures),
    "failed": len(failures),
    "failures": failures,
    "checks": results,
}
OUT_JSON.write_text(json.dumps(report, indent=1))
print(f"\n{'=' * 70}")
print(f"DOWNLOAD VERIFIER: {len(results)} endpoints checked, "
      f"{len(failures)} FAILED")
if failures:
    print("\nFAILURES:")
    for f in failures:
        print(f"  {f['label']}: {'; '.join(f['problems'])}")
        if f.get("body_head"):
            print(f"    body: {f['body_head'][:160]}")
print(f"\nfull JSON: {OUT_JSON}")
sys.exit(1 if failures else 0)
