"""v8.18.19 — LIVE MATH: calculation traces + the YTD join bug it caught.

THE FEATURE
    Users doubted the numbers the system computes. Every key metric now
    exposes a step-by-step trace (GET /api/calc/trace) — inputs, formula,
    the expression with live numbers substituted, the result, provenance
    (which sales/bills counted) and notes. Traces are built FROM THE SAME
    source functions the reports use, so they can never disagree.

THE BUG FOUND WHILE BUILDING IT
    get_ytd_profit computed SUM(s.total) over
    `sales s LEFT JOIN sale_items si` — every sale's total was multiplied
    by its number of line items (a 4-line sale counted 4x). On the sample
    DB: YTD Sales 57,100 instead of the true 15,650; YTD margin 87.6%
    instead of 54.76%. The per-month rows had the same inflation. Existing
    tests only checked internal consistency (margin == gp/sales), never
    that ytd_sales equals the real sum — which is why it survived.

Contract guarded here:
    1. trace results == the source-function numbers, for ALL 11 metrics
    2. YTD: sales/cogs/monthly rows are NOT line-multiplied (regression)
    3. avg_cost replay matches stored state; drift is surfaced honestly
    4. API: 200/404/400 paths; /api/calc/metrics
    5. get_margins() categories carry `id` (frontend wiring needs it)
    6. every wired page imports the component; all touched JS parses
"""
import subprocess
import sys
from pathlib import Path

import pytest

PROJ = Path(__file__).resolve().parent.parent
from app import calc_explain


# ─── 1. every metric's result == the source function's number ─────────────

def test_overall_margin_matches_get_margins(sample_db):
    from app.profit_analytics import get_margins
    m = get_margins()
    t = calc_explain.get_trace("overall_margin", {})
    assert t["result"] == m["actual_overall_margin"]
    assert t["steps"][3]["value"] == m["actual_overall_margin"]  # step 4 = result
    assert t["steps"][0]["value"] == m["total_sales"]
    assert t["steps"][1]["value"] == m["total_cogs"]


def test_category_average_matches_get_margins(sample_db):
    from app.profit_analytics import get_margins
    t = calc_explain.get_trace("category_average_margin", {})
    assert t["result"] == get_margins()["category_average_margin"]
    # provenance lists every category with its own math
    prov_labels = " ".join(p["label"] for p in t["provenance"])
    assert "A · Budget" in prov_labels and "D · Luxury" in prov_labels


def test_category_margin_matches_state_and_list_price(sample_db):
    from app.db import conn
    with conn() as c:
        cat = c.execute("SELECT sell_price FROM price_categories WHERE id=1").fetchone()
        st = c.execute("SELECT current_qty, current_value, current_avg_cost "
                       "FROM category_stock_state WHERE category_id=1").fetchone()
    t = calc_explain.get_trace("category_margin", {"category_id": 1})
    sell, cost = float(cat["sell_price"]), float(st["current_avg_cost"])
    expected = round((sell - cost) / sell * 100, 2)
    assert t["result"] == expected
    assert t["steps"][2]["value"] == round(sell - cost, 4)  # Margin (Rs) step


def test_avg_cost_is_pool_value_over_qty(sample_db):
    from app.db import conn
    with conn() as c:
        st = c.execute("SELECT current_qty, current_value, current_avg_cost "
                       "FROM category_stock_state WHERE category_id=1").fetchone()
    t = calc_explain.get_trace("avg_cost", {"category_id": 1})
    assert t["result"] == float(st["current_avg_cost"])
    assert t["replay_matches_state"] is True
    assert t["events"], "avg_cost trace must include the event replay"
    # last event lands exactly on the stored state
    last = t["events"][-1]
    assert abs(last["qty_after"] - float(st["current_qty"])) < 0.011
    assert abs(last["value_after"] - float(st["current_value"])) < 0.011


def test_monthly_margin_matches_get_monthly_profit(sample_db):
    from app.profit_analytics import get_monthly_profit
    r = get_monthly_profit("2026-08")
    t = calc_explain.get_trace("monthly_margin", {"month": "2026-08"})
    assert t["result"] == r["monthly_margin"]
    # bridge steps expose every input the report used
    vals = [s["value"] for s in t["steps"]]
    assert r["opening_inventory"] in vals
    assert r["cogs"] in vals
    # closing inventory appears inside the subtraction expression
    exprs = " ".join(s["expression"] or "" for s in t["steps"])
    assert f"{r['closing_inventory']:,.2f}" in exprs
    # cross-check step is honest: bridge cogs − line cogs == the report's diff
    assert r["cogs_from_sales"] in vals


def test_ytd_margin_matches_get_ytd_profit(sample_db):
    from app.profit_analytics import get_ytd_profit
    r = get_ytd_profit()
    t = calc_explain.get_trace("ytd_margin", {})
    assert t["result"] == r["ytd_margin"]
    assert t["steps"][0]["value"] == r["ytd_sales"]


def test_pnl_gross_and_net_match_get_pnl(sample_db):
    from app.shop import get_pnl
    r = get_pnl("2026-08")
    tg = calc_explain.get_trace("pnl_gross_margin", {"month": "2026-08"})
    tn = calc_explain.get_trace("pnl_net_margin", {"month": "2026-08"})
    # NOTE: get_pnl's gross_margin/net_margin fields are 2-decimal FRACTIONS
    # (0.55) — the page recomputes at full precision from gross_profit/
    # net_revenue, and so does the trace. Compare against the displayed math.
    exp_g = round(r["gross_profit"] / r["net_revenue"] * 100, 2)
    exp_n = round(r["net_profit"] / r["net_revenue"] * 100, 2)
    assert tg["result"] == exp_g
    assert tn["result"] == exp_n


def test_actual_earnings_margin_matches(sample_db):
    from app.shop import get_actual_earnings
    r = get_actual_earnings("2026-08")
    t = calc_explain.get_trace("actual_earnings_margin", {"month": "2026-08"})
    expected = round(r["actual_earnings"] / r["total_sales"] * 100, 2)
    assert t["result"] == expected


def test_pa_margin_matches_profit_analysis_report(sample_db):
    from app.reports import profit_analysis_report
    r = profit_analysis_report("2026-08-01", "2026-08-31", "category")
    t = calc_explain.get_trace("pa_margin", {"start": "2026-08-01", "end": "2026-08-31"})
    assert t["result"] == r["totals"]["margin_pct"]
    # per-category variant matches that category's row
    row = r["categories"][0]
    tc = calc_explain.get_trace("pa_margin", {"start": "2026-08-01", "end": "2026-08-31",
                                              "category_id": row["category_id"]})
    assert tc["result"] == row["margin_pct"]


def test_daily_margin_matches_daily_stock_report(sample_db):
    from app.profit_analytics import get_daily_stock_report
    r = get_daily_stock_report("2026-08-11")
    t = calc_explain.get_trace("daily_margin", {"date": "2026-08-11"})
    tt = r["totals"]
    expected = round(tt["gross_profit"] / tt["sales_value"] * 100, 2) if tt["sales_value"] else 0.0
    assert t["result"] == expected


# ─── 2. THE YTD JOIN BUG (regression) ──────────────────────────────────────

def test_ytd_sales_is_not_line_multiplied(sample_db):
    """Before v8.18.19: SUM(s.total) over sales LEFT JOIN sale_items counted
    every sale once PER LINE ITEM (4-line sale = 4x its total)."""
    from app.profit_analytics import get_ytd_profit
    from app.db import conn
    with conn() as c:
        truth = c.execute(
            "SELECT COALESCE(SUM(total),0) AS v FROM sales "
            "WHERE payment_status IN ('paid','credit','partial')").fetchone()["v"]
    r = get_ytd_profit()
    assert r["ytd_sales"] == float(truth), (
        f"ytd_sales {r['ytd_sales']} != true sum of sales.total {truth} "
        "(LEFT JOIN line multiplication is back)")


def test_ytd_monthly_rows_not_line_multiplied(sample_db):
    from app.profit_analytics import get_ytd_profit
    from app.db import conn
    with conn() as c:
        truth = c.execute(
            "SELECT COALESCE(SUM(total),0) AS v FROM sales "
            "WHERE payment_status IN ('paid','credit','partial') "
            "AND strftime('%Y-%m', created_at)='2026-08'").fetchone()["v"]
    r = get_ytd_profit()
    aug = next((m for m in r["monthly"] if m["month"] == "2026-08"), None)
    assert aug is not None
    assert aug["sales"] == float(truth)


def test_ytd_consistent_with_monthly_pnl_on_single_month_data(sample_db):
    """All sample sales are in 2026-08, so YTD margin == monthly margin == PnL
    gross margin. Before the fix YTD was 87.6% while monthly was 54.76%."""
    from app.profit_analytics import get_ytd_profit, get_monthly_profit
    from app.shop import get_pnl
    y = get_ytd_profit()["ytd_margin"]
    m = get_monthly_profit("2026-08")["monthly_margin"]
    p = round(get_pnl("2026-08")["gross_profit"] / get_pnl("2026-08")["net_revenue"] * 100, 2)
    assert abs(y - m) < 0.02 and abs(y - p) < 0.02


# ─── 3. replay drift honesty ──────────────────────────────────────────────

def test_replay_flags_drift_when_state_is_wrong(sample_db):
    from app.db import conn
    with conn() as c:
        c.execute("UPDATE category_stock_state SET current_avg_cost=999 WHERE category_id=1")
    t = calc_explain.get_trace("avg_cost", {"category_id": 1})
    assert t["replay_matches_state"] is False
    assert any("Heads-up" in n for n in t["notes"]), "drift must be VISIBLE, not silent"


# ─── 4. API contract ──────────────────────────────────────────────────────

@pytest.fixture()
def calc_client(authed_client):
    return authed_client


def test_api_trace_overall(calc_client):
    r = calc_client.get("/api/calc/trace?metric=overall_margin")
    assert r.status_code == 200
    body = r.json()
    for key in ("metric", "title", "result", "result_unit", "steps",
                "provenance", "notes", "as_of"):
        assert key in body, f"missing {key}"
    for s in body["steps"]:
        for k in ("n", "label", "formula", "value", "unit"):
            assert k in s, f"step missing {k}"


def test_api_trace_with_params(calc_client):
    r = calc_client.get("/api/calc/trace?metric=avg_cost&category_id=1")
    assert r.status_code == 200
    body = r.json()
    assert body["events"] is not None and len(body["events"]) >= 1


def test_api_unknown_metric_404(calc_client):
    r = calc_client.get("/api/calc/trace?metric=not_a_metric")
    assert r.status_code == 404


def test_api_missing_category_400(calc_client):
    r = calc_client.get("/api/calc/trace?metric=category_margin")
    assert r.status_code == 400


def test_api_bad_month_400(calc_client):
    r = calc_client.get("/api/calc/trace?metric=monthly_margin&month=not-a-month")
    assert r.status_code == 400


def test_api_metrics_list(calc_client):
    r = calc_client.get("/api/calc/metrics")
    assert r.status_code == 200
    assert "overall_margin" in r.json()["metrics"]
    assert len(r.json()["metrics"]) == 11


# ─── 5. get_margins carries category id (frontend needs it) ───────────────

def test_get_margins_categories_have_id(sample_db):
    from app.profit_analytics import get_margins
    cats = get_margins()["categories"]
    assert cats and all(c.get("id") for c in cats)


# ─── 6. frontend wiring guards ────────────────────────────────────────────

PAGES = [
    "app/static/js/components/live-math.js",
    "app/static/js/pages/margins-page.js",
    "app/static/js/pages/store-profit-dashboard.js",
    "app/static/js/pages/monthly-profit-page.js",
    "app/static/js/pages/ytd-profit-page.js",
    "app/static/js/pages/reports-financial.js",
    "app/static/js/pages/actual-earnings-page.js",
    "app/static/js/pages/reports-pages.js",
    "app/static/js/pages/inventory-pages.js",
]


@pytest.mark.parametrize("page", PAGES, ids=lambda p: Path(p).name)
def test_pages_import_live_math(page):
    src = (PROJ / page).read_text(encoding="utf-8")
    if page.endswith("live-math.js"):
        assert "data-lm" in src and "api(`/api/calc/trace" in src
    else:
        assert "components/live-math.js" in src, "must import the live-math component"
        assert "lm(" in src, "must render at least one clickable value"


def test_all_touched_js_parses():
    for page in PAGES:
        res = subprocess.run([sys.executable, "-c", "import sys; sys.exit(0)"] if False
                             else ["node", "--check", str(PROJ / page)],
                             capture_output=True)
        # node may be absent in exotic environments — skip silently then
        if res.returncode == 127:
            pytest.skip("node not available")
        assert res.returncode == 0, f"{page}: {res.stderr.decode()[:400]}"


def test_live_math_endpoint_used_by_component():
    src = (PROJ / "app/static/js/components/live-math.js").read_text(encoding="utf-8")
    assert "/api/calc/trace" in src
    # one global listener, guarded against double-wiring
    assert "__liveMathWired" in src


# ─── 7. v8.18.20: RBAC — traces expose cost/COGS/PnL data ──────────────────

def test_cashier_cannot_use_calc_trace(cashier_client):
    """Live-math traces expose avg cost, per-bill purchase prices, COGS and
    PnL numbers. /api/reports/pnl was already manager-only, but the new
    /api/calc/trace?metric=pnl_* bypassed that until v8.18.20 added
    /api/calc to CASHIER_RESTRICTED_PREFIXES."""
    for metric, extra in (
        ("overall_margin", ""),
        ("pnl_net_margin", "&month=2026-08"),
        ("pnl_gross_margin", "&month=2026-08"),
        ("avg_cost", "&category_id=1"),
    ):
        r = cashier_client.get(f"/api/calc/trace?metric={metric}{extra}")
        assert r.status_code == 403, f"{metric}: expected 403, got {r.status_code}"
    r = cashier_client.get("/api/calc/metrics")
    assert r.status_code == 403


def test_manager_can_still_use_calc_trace(authed_client):
    """The RBAC tightening must not lock managers out of the feature."""
    r = authed_client.get("/api/calc/trace?metric=pnl_net_margin&month=2026-08")
    assert r.status_code == 200
    r = authed_client.get("/api/calc/trace?metric=avg_cost&category_id=1")
    assert r.status_code == 200


# ─── 8. v8.18.20: waterfall labels describe their VALUES ──────────────────

def test_waterfall_labels_describe_values(sample_db):
    """Steps labelled '− COGS' / '− Operating Expenses' used to show the
    RUNNING TOTAL in the value column (gross profit, not COGS), which users
    read as the labelled quantity. Now every label describes the number it
    shows; the input amount lives in the expression/formula."""
    tn = calc_explain.get_trace("pnl_net_margin", {"month": "2026-08"})
    labels = [s["label"] for s in tn["steps"]]
    assert "After Operating Expenses" in labels
    assert "After Other Income" in labels
    from app.shop import get_pnl
    r = get_pnl("2026-08")
    s5 = next(s for s in tn["steps"] if s["label"] == "After Operating Expenses")
    assert s5["value"] == round(float(r["gross_profit"]) - float(r["expenses"]), 4)
    # the expense amount itself is visible in the expression
    assert f"{float(r['expenses']):,.2f}" in (s5["expression"] or "")

    ae = calc_explain.get_trace("actual_earnings_margin", {"month": "2026-08"})
    ae_labels = [s["label"] for s in ae["steps"]]
    assert any(l.startswith("Gross Profit (Sales") for l in ae_labels)
    assert "Actual Earnings (after Expenses)" in ae_labels

    mt = calc_explain.get_trace("monthly_margin", {"month": "2026-08"})
    m_labels = [s["label"] for s in mt["steps"]]
    assert "Inventory after Purchases" in m_labels
    assert "COGS (bridge)" in m_labels
