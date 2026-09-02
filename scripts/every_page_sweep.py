#!/usr/bin/env python3
# ═══════════════════════════════════════════════════════════════════
# EVERY-PAGE runtime sweep (v8.18.10-v8.18.11 whole-system verification)
#
# "Run it against every page in the system":
#   1. Boots the REAL backend (uvicorn + license-bypass E2E wrapper)
#      on a temp DB seeded with sample data.
#   2. Logs in through the REAL login page.
#   3. Visits EVERY registered SPA route (enumerated from JS source,
#      83 literal + 5 param routes).
#   4. Per page, captures the exact bug class that broke
#      /reports/monthly-close:
#        a. HTTP failures  — API calls returning 404/5xx (dead endpoints)
#        b. DEAD FIELD READS — page JS reads r.<field> the API never
#           returns (recorded by a fetch/JSON-Proxy probe injected
#           before app JS runs; presence checked LIVE against the
#           parsed response object, so 'accessed but never in payload'
#           is exact ground truth)
#        c. pageerror / console.error — runtime JS exceptions
#        d. DOM verdict — did the page render or show an error state
#
# No production file is touched. Run from the repo root (deps: playwright
# with `playwright install chromium`, uvicorn, bcrypt — same env as the app):
#   python scripts/every_page_sweep.py
# ═══════════════════════════════════════════════════════════════════
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]   # repo root (parent of scripts/)
JS_ROOT = PROJ / 'app/static/js'
PORT = 8821
BASE = f"http://127.0.0.1:{PORT}"
OUT_JSON = Path(__file__).resolve().parent / 'every_page_sweep_results.json'


def _pkill(pattern: str):
    """Kill stray uvicorn children. POSIX-only (no pkill binary on Windows);
    non-fatal — server.terminate()/kill() cleans up the tree regardless."""
    if os.name == "posix":
        subprocess.run(["pkill", "-9", "-f", pattern], capture_output=True)

# ── 1. enumerate every registered route from the JS source ──────────
ROUTE_RE = re.compile(r"""\broute\(\s*(['"`])(/[^'"`]*?)\1""")
COMMENT_RE = re.compile(r"^\s*//")
routes = {}
for jsf in sorted(JS_ROOT.rglob('*.js')):
    src = jsf.read_text(encoding='utf-8', errors='replace')
    for m in ROUTE_RE.finditer(src):
        line = src[:m.start()].count('\n') + 1
        # skip doc-comment examples (e.g. list-state.js '/things' usage doc)
        if COMMENT_RE.match(src.split('\n')[line - 1]):
            continue
        routes.setdefault(m.group(2), []).append(str(jsf.relative_to(JS_ROOT)))
literal_routes = sorted(p for p in routes if '${' not in p)
# prefix-handler param routes -> visit with real sample-data ids
PARAM_ROUTES = ['/bills/1', '/customers/1', '/suppliers/1', '/pos/sale/1',
                '/purchase-orders/1']
ALL_ROUTES = literal_routes + PARAM_ROUTES
print(f"[enum] {len(literal_routes)} literal routes + {len(PARAM_ROUTES)} param routes = {len(ALL_ROUTES)} pages")

# ── 2. temp DB + sample data (reuse the proven E2E seeding) ─────────
data_dir = tempfile.mkdtemp(prefix="bb_sweep_")
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
    levels = {'auto_confirm_bills': 3, 'auto_draft_po': 2, 'urdhaar_reminders': 1,
              'recurring_detection': 1, 'expense_categorization': 2,
              'anomaly_diagnosis': 1, 'variance_investigation': 1,
              'scheduled_reports': 1, 'dead_stock_liquidation': 2,
              'ai_kill_switch': 0}
    for key, level in levels.items():
        c.execute("INSERT OR REPLACE INTO automation_config(key,enabled,level,params_json)"
                  " VALUES(?,?,?,?)", (key, 0, level, '{}'))
    c.execute("INSERT INTO cash_drawer(type, amount, description) VALUES('opening', 50000, 'sweep')")
    c.execute("INSERT INTO employees(name, role, pin, active) VALUES('Sweep Mgr', 'manager', '1234', 1)")
    # one purchase order so /purchase-orders/1 exercises the real detail path
    c.execute("INSERT INTO purchase_orders(id, po_no, supplier_id, supplier_name, status, "
              "total, notes, expected_date) VALUES(1, 'PO-0001', 1, 'E2E Supplies', 'sent', "
              "950, 'sweep seed', '2026-09-10')")
    c.execute("INSERT INTO purchase_order_items(po_id, item_name, qty, est_price, line_total) "
              "VALUES(1, 'E2E Reorder Widget', 10, 95, 950)")
    # reorder pattern (proven in v8_18_10 reorder E2E) so /reorder has rows
    for i, d in enumerate(["2026-05-22", "2026-06-06", "2026-06-21", "2026-07-01"]):
        c.execute("INSERT INTO bills(id, supplier_id, supplier_name, bill_date, bill_no, "
                  "written_total, computed_total, status, payment_status, created_at) "
                  "VALUES(?,?,?,?,?,?,?,?,?,?)",
                  (901 + i, 1, "E2E Supplies", d, f"E2E-W{i}", 1000, 1000,
                   "confirmed", "paid", f"{d} 10:00:00"))
        c.execute("INSERT INTO bill_items(bill_id, category_id, raw, item_code, price, "
                  "qty, unit, line_total, page_no) VALUES(?,?,?,?,?,?,?,?,?)",
                  (901 + i, 1, "E2E Reorder Widget", "A", 95, 10, "pcs", 950, 1))
    c.execute("DELETE FROM settings WHERE key IN ('password_hash','setup_completed','start_page')")
    c.execute("INSERT INTO settings(key, value) VALUES(?,?)",
              ("password_hash", hash_password("testpass")))
    c.execute("INSERT INTO settings(key, value) VALUES('setup_completed','true')")
    c.execute("INSERT INTO settings(key, value) VALUES('start_page','launcher')")
print(f"[setup] DB at {data_dir}")

# ── 3. license-bypass E2E wrapper + uvicorn (one process tree) ──────
_pkill(f"uvicorn.*--port {PORT}")
time.sleep(0.5)
wrapper = Path(data_dir) / "e2e_wrapper.py"
wrapper.write_text(
    "# E2E ONLY — never shipped. Patches the license gate before app.main loads.\n"
    "# Also neutralizes the global API throttle (200 req/60s) — the sweep makes\n"
    "# ~350 API calls in ~3 minutes and would otherwise self-trip 429s.\n"
    "import app.licensing as _lic\n"
    "_lic.is_activated = lambda: True\n"
    "_lic.license_state = lambda: {'required': True, 'activated': True, "
    "'setup_id': 'E2E', 'license': None, 'reason': None}\n"
    "import asyncio as _aio\n"
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

# ── 4. the probe: fetch wrapper + JSON proxy recording dead reads ────
PROBE_JS = r"""
(() => {
  if (window.__bbProbe) return;
  const P = window.__bbProbe = { calls: [], dead: [] };
  const NOISE = new Set(['then','catch','finally','toJSON','length','constructor',
    'prototype','__proto__','hasOwnProperty','toString','valueOf','inspect',
    'nodeType','tagName','__bbProbe',
    // `error` is a legitimate optional key across this API (several endpoints
    // return {error: ...} with HTTP 200) — frontend `if (r.error)` guards
    // are correct defensive handling, not dead reads.
    'error']);
  const pageOf = () => { const h = (location.hash || '#/').slice(1).split('?')[0]; return h || '/'; };
  const cache = new WeakMap();
  function wrap(v, url, depth) {
    if (v === null || v === undefined) return v;
    const t = typeof v;
    if (t !== 'object') return v;              // primitives + functions untouched
    if (depth > 4) return v;
    if (cache.has(v)) return cache.get(v);
    let out;
    if (Array.isArray(v)) {
      out = v.map(e => wrap(e, url, depth + 1)); // real array, elements proxied
    } else {
      out = new Proxy(v, {
        get(t, k) {
          if (typeof k === 'string' && !NOISE.has(k) && !(k in t)) {
            try { P.dead.push({ page: pageOf(), url, key: k }); } catch (e) {}
          }
          return wrap(Reflect.get(t, k), url, depth + 1);
        }
      });
    }
    cache.set(v, out);
    return out;
  }
  const origFetch = window.fetch.bind(window);
  window.fetch = async function(input, init) {
    const url = typeof input === 'string' ? input : (input && input.url) || String(input);
    const method = ((init && init.method) || 'GET').toUpperCase();
    const res = await origFetch(input, init);
    try {
      P.calls.push({ page: pageOf(), method, url, status: res.status });
      if ((res.headers.get('content-type') || '').includes('json')
          && res.status >= 200 && res.status < 300) {
        const origJson = res.json.bind(res);
        res.json = async () => wrap(await origJson(), url, 0);
      }
    } catch (e) {}
    return res;
  };
})();
"""

from playwright.sync_api import sync_playwright

results = []
with sync_playwright() as pw:
    browser = pw.chromium.launch()
    ctx = browser.new_context(viewport={"width": 1360, "height": 900})
    ctx.add_init_script(PROBE_JS)
    page = ctx.new_page()
    pageerrors, console_errs = [], []
    page.on("pageerror", lambda e: pageerrors.append((page.url, str(e))))
    page.on("console", lambda m: console_errs.append((page.url, m.text)) if m.type == "error" else None)

    # ── login through the real login page ──
    page.goto(f"{BASE}/login", wait_until="networkidle")
    page.fill("#p", "testpass")
    page.click(".login-btn")
    page.wait_for_selector(".launcher-root, #page", timeout=20000)
    page.wait_for_timeout(600)
    print(f"[login] ok -> {page.url}")

    def still_logged_in():
        return "/login" not in page.url

    def relogin():
        page.goto(f"{BASE}/login", wait_until="networkidle")
        page.fill("#p", "testpass")
        page.click(".login-btn")
        page.wait_for_selector(".launcher-root, #page", timeout=20000)
        page.wait_for_timeout(400)

    for route in ALL_ROUTES:
        rec = {"route": route, "files": routes.get(route, [])}
        try:
            page.goto(f"{BASE}#{route}")
            if not still_logged_in():
                relogin()
                page.goto(f"{BASE}#{route}")
            try:
                page.wait_for_load_state("networkidle", timeout=6000)
            except Exception:
                pass
            page.wait_for_timeout(800)
            probe = page.evaluate("() => JSON.parse(JSON.stringify(window.__bbProbe))")
            def belongs(entry):
                p = entry.get("page", "")
                p = p.split("?")[0]
                return p == route or p.rstrip("/") == route.rstrip("/")
            rec["failures"] = [c for c in probe["calls"]
                               if c["status"] >= 400 and belongs(c)]
            rec["dead_reads"] = [d for d in probe["dead"] if belongs(d)]
            rec["n_calls"] = len([c for c in probe["calls"] if belongs(c)])
            rec["pageerrors"] = [e[1] for e in pageerrors if e[0].endswith(route) or f"#{route}" in e[0]]
            rec["console_errors"] = [t for u, t in console_errs if f"#{route}" in u]
            rec["dom"] = page.evaluate("""() => {
              const p = document.querySelector('#page') || document.querySelector('#kiosk-page')
                     || document.querySelector('.launcher-root');
              if (!p) return {dom: 'NO-CONTAINER'};
              const txt = (p.innerText || '').trim();
              const empty = p.querySelector('.empty-state');
              return { dom: 'OK', text_len: txt.length,
                       empty_state: empty ? empty.innerText.trim().slice(0, 100) : null,
                       error_page: /^\\s*(Error|Page not found)/i.test(txt) };
            }""")
        except Exception as e:
            rec["exception"] = str(e)[:200]
        results.append(rec)
        flag = "!" if (rec.get("failures") or rec.get("pageerrors")
                       or rec.get("exception") or rec.get("dead_reads")) else "."
        f = [x for x in rec.get("failures", []) if x["status"] != 429]
        d = rec.get("dead_reads", [])
        pe = rec.get("pageerrors", [])
        print(f"  [{flag}] {route:42s} calls={rec.get('n_calls', 0):3d} "
              f"4xx/5xx={len(f)} dead={len(d)} jserr={len(pe)}"
              + (f"  <- {f[0]['url']} {f[0]['status']}" if f else "")
              + (f"  <- {pe[0][:60]}" if pe else ""))
    browser.close()

# ── 5. teardown + report ─────────────────────────────────────────────
server.terminate()
try:
    server.wait(timeout=5)
except Exception:
    server.kill()
_pkill(f"uvicorn.*--port {PORT}")

# aggregate (429s are sweep self-throttling noise — excluded; with the
# E2E wrapper bypassing APIThrottleMiddleware none should appear)
THROTTLE = lambda c: c["status"] == 429
pages_with_failures = [r for r in results if [c for c in r.get("failures", []) if not THROTTLE(c)]]
pages_with_dead = [r for r in results if r.get("dead_reads")]
pages_with_jserr = [r for r in results if r.get("pageerrors") or r.get("exception")]

fail_urls = {}
for r in pages_with_failures:
    for c in r["failures"]:
        if THROTTLE(c):
            continue
        fail_urls.setdefault(f"{c['method']} {c['url']} -> {c['status']}", []).append(r["route"])
dead_pairs = {}
for r in pages_with_dead:
    for d in r["dead_reads"]:
        dead_pairs.setdefault(f"{d['url']} .{d['key']}", set()).add(r["route"])

report = {
    "visited": len(results),
    "summary": {
        "pages_ok": len(results) - len(pages_with_failures) - len(pages_with_dead) - len(pages_with_jserr),
        "pages_with_http_failures": len(pages_with_failures),
        "pages_with_dead_reads": len(pages_with_dead),
        "pages_with_js_errors": len(pages_with_jserr),
        "distinct_failing_endpoints": {k: sorted(set(v)) for k, v in fail_urls.items()},
        "distinct_dead_reads": {k: sorted(v) for k, v in dead_pairs.items()},
    },
    "pages": results,
}
OUT_JSON.write_text(json.dumps(report, indent=1))

print(f"\n{'=' * 70}")
print(f"SWEEP COMPLETE: {len(results)} pages visited")
print(f"  clean pages          : {report['summary']['pages_ok']}")
print(f"  pages w/ 4xx/5xx     : {len(pages_with_failures)}")
print(f"  pages w/ dead reads  : {len(pages_with_dead)}")
print(f"  pages w/ JS errors   : {len(pages_with_jserr)}")
if fail_urls:
    print("\nFAILING ENDPOINTS:")
    for k, v in sorted(fail_urls.items()):
        print(f"  {k}   pages={sorted(set(v))[:4]}")
if dead_pairs:
    print("\nDEAD FIELD READS (key never present in payload):")
    for k, v in sorted(dead_pairs.items()):
        print(f"  {k}   pages={sorted(v)[:4]}")
print(f"\nfull JSON: {OUT_JSON}")
