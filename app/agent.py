"""v7.0 Phase 3-4 — Agent Core: READ tools, WRITE tools, constrained SQL.

The agent uses a tool-calling loop where READ tools auto-execute (wrapping
existing endpoints so business math is never recomputed by the LLM) and
WRITE tools create pending_actions (never execute directly).

v8.4: Real LLM tool-calling via Groq. When a Groq API key is configured,
the LLM decides which tools to call based on the user's question — no more
keyword heuristic. Falls back to heuristic only when no key is available.

Governing principle: AI prepares; the owner decides.
"""
import logging
import json, logging, re, sqlite3, os
from datetime import datetime
from . import db as _db
from .db import conn

logger = logging.getLogger(__name__)

# ─── READ TOOLS (auto-execute, read-only) ──────────────────────────────────

def _tool_get_sales_summary(params: dict) -> dict:
    from . import shop
    return shop.get_daily_summary(params.get("date"))

def _tool_get_margins(params: dict) -> dict:
    from .profit import get_margins
    return get_margins()

def _tool_get_monthly_profit(params: dict) -> dict:
    from .profit import get_monthly_profit
    return get_monthly_profit(params.get("month", ""))

def _tool_get_ytd(params: dict) -> dict:
    from .profit import get_ytd_profit
    return get_ytd_profit()

def _tool_get_cash_buckets(params: dict) -> dict:
    from .profit import get_cash_buckets
    return get_cash_buckets(params.get("date", ""))

def _tool_get_break_even(params: dict) -> dict:
    from .extensions import get_break_even
    return get_break_even()

def _tool_get_inventory_state(params: dict) -> dict:
    from . import shop
    return {"items": shop.get_inventory()}

def _tool_get_stock_reserve(params: dict) -> dict:
    from .profit import get_stock_reserve
    return get_stock_reserve()

def _tool_get_lost_sales_summary(params: dict) -> dict:
    from .extensions import get_lost_sales_summary
    return get_lost_sales_summary(params.get("month", ""))

def _tool_get_margin_alerts(params: dict) -> dict:
    from .extensions import get_margin_alerts
    return {"alerts": get_margin_alerts()}

def _tool_get_customer_credit_top(params: dict) -> dict:
    with conn() as c:
        rows = c.execute(
            "SELECT id, name, phone, total_credit FROM customers "
            "WHERE total_credit > 0 AND deleted_at IS NULL ORDER BY total_credit DESC LIMIT ?",
            (params.get("limit", 10),)
        ).fetchall()
    return {"customers": [dict(r) for r in rows]}

def _tool_get_expenses_summary(params: dict) -> dict:
    from . import shop
    return shop.get_expense_summary(params.get("month", ""))

def _tool_get_daily_stock(params: dict) -> dict:
    from .profit import get_daily_stock_report
    return get_daily_stock_report(params.get("date", ""))

def _tool_get_shift_variances(params: dict) -> dict:
    from . import shop
    return {"variances": shop.get_employee_variance_history(params.get("employee_id", 1))}

def _tool_get_trend_signals(params: dict) -> dict:
    from .trends import get_trend_alerts
    return {"alerts": get_trend_alerts()}

def _tool_get_owner_hub(params: dict) -> dict:
    """v8.0: Get the consolidated Owner Hub dashboard (HQ-side multi-branch summary)."""
    from .routers.hq import owner_hub_dashboard
    return owner_hub_dashboard(date=params.get("date", ""))

def _tool_get_branches(params: dict) -> dict:
    """v8.0: Get the list of registered branches (HQ-side registry)."""
    from .routers.hq import list_branches
    return list_branches(active_only=bool(params.get("active_only", True)))

def _tool_get_transfers(params: dict) -> dict:
    """v8.0: Get transfer challans (inter-branch stock movements)."""
    from .routers.transfers import list_transfers
    return list_transfers(
        status=params.get("status", ""),
        direction=params.get("direction", ""),
        limit=int(params.get("limit", 20)),
    )

def _tool_web_search(params: dict) -> dict:
    """v8.4: Search the web for real-time information (market prices, competitors, news, etc.).

    Uses the z-ai CLI's built-in web_search function (powered by ZAI's in-house
    search service). Returns top 5 results with title, URL, and snippet.
    Falls back to DuckDuckGo API if z-ai CLI is unavailable.
    """
    query = params.get("query", "").strip()
    if not query:
        return {"error": "No search query provided", "results": []}
    try:
        import subprocess, shutil
        # Primary: use z-ai CLI (ZAI in-house search service)
        z_ai_bin = shutil.which("z-ai")
        if z_ai_bin:
            args = json.dumps({"query": query, "num": 5})
            proc = subprocess.run(
                [z_ai_bin, "function", "--name", "web_search", "--args", args],
                capture_output=True, text=True, timeout=20
            )
            if proc.returncode == 0 and proc.stdout:
                # Parse the JSON output from z-ai CLI
                output = proc.stdout.strip()
                # The CLI prints some status lines + JSON — find the JSON array
                json_start = output.find('[')
                json_end = output.rfind(']') + 1
                if json_start >= 0 and json_end > json_start:
                    raw_results = json.loads(output[json_start:json_end])
                    results = []
                    for r in raw_results[:5]:
                        results.append({
                            "title": r.get("name", r.get("title", ""))[:200],
                            "url": r.get("url", ""),
                            "snippet": r.get("snippet", "")[:300],
                        })
                    if results:
                        return {"query": query, "results": results, "count": len(results)}
            # If z-ai failed, fall through to DuckDuckGo

        # Fallback: DuckDuckGo API (no key needed, but limited results)
        import httpx
        import urllib.parse
        encoded_q = urllib.parse.quote(query)
        r = httpx.get(
            f"https://api.duckduckgo.com/?q={encoded_q}&format=json&no_html=1",
            timeout=15, headers={"User-Agent": "Mozilla/5.0"}
        )
        data = r.json()
        results = []
        # Check AbstractText first
        if data.get("AbstractText"):
            results.append({
                "title": data.get("Heading", query),
                "url": data.get("AbstractURL", ""),
                "snippet": data["AbstractText"][:300],
            })
        # Check RelatedTopics
        for t in data.get("RelatedTopics", [])[:5]:
            if isinstance(t, dict) and "Text" in t:
                results.append({
                    "title": t["Text"][:100],
                    "url": t.get("FirstURL", ""),
                    "snippet": t["Text"][:300],
                })
        if results:
            return {"query": query, "results": results, "count": len(results)}
        return {"query": query, "results": [], "message": "No web results found. Try a different search query."}
    except Exception as e:
        return {"error": f"Web search failed: {e}", "results": []}

READ_TOOLS = {
    "get_sales_summary": _tool_get_sales_summary,
    "get_margins": _tool_get_margins,
    "get_monthly_profit": _tool_get_monthly_profit,
    "get_ytd": _tool_get_ytd,
    "get_cash_buckets": _tool_get_cash_buckets,
    "get_break_even": _tool_get_break_even,
    "get_inventory_state": _tool_get_inventory_state,
    "get_stock_reserve": _tool_get_stock_reserve,
    "get_lost_sales_summary": _tool_get_lost_sales_summary,
    "get_margin_alerts": _tool_get_margin_alerts,
    "get_customer_credit_top": _tool_get_customer_credit_top,
    "get_expenses_summary": _tool_get_expenses_summary,
    "get_daily_stock": _tool_get_daily_stock,
    "get_shift_variances": _tool_get_shift_variances,
    "get_trend_signals": _tool_get_trend_signals,
    "get_owner_hub": _tool_get_owner_hub,
    "get_branches": _tool_get_branches,
    "get_transfers": _tool_get_transfers,
    "web_search": _tool_web_search,
}

# OpenAI-style tool schemas for the LLM — each tool has a detailed description
# so the LLM can intelligently decide which tool(s) to call based on the user's question.
TOOL_DESCRIPTIONS = {
    "get_sales_summary": "Get today's sales summary — total sales, cash, credit, items sold. Use for 'how much did I sell today' or 'today's sales'.",
    "get_margins": "Get actual overall gross margin % and per-category margins (sell_price, avg_cost, margin_pct). Use for 'what is my margin', 'profit margin', 'overall margin', 'how is my margin calculated', 'category A margin', 'why is my margin low'.",
    "get_monthly_profit": "Get this month's P&L — sales, COGS, gross profit, operating profit, opening/closing inventory. Use for 'profit this month', 'monthly profit', 'earnings', 'how much did I earn'.",
    "get_ytd": "Get year-to-date profit — cumulative sales, GP, margin. Use for 'YTD', 'year to date', 'annual profit'.",
    "get_cash_buckets": "Get cash position — cash in drawer, available for withdrawal, stock reserve. Use for 'how much cash', 'withdraw', 'cash buckets', 'how much can I take out'.",
    "get_break_even": "Get break-even daily target and how much sold so far today. Use for 'break-even', 'daily target', 'how much do I need to sell'.",
    "get_inventory_state": "Get current stock levels for all categories — qty, avg_cost, value, negative stock, low_stock flag. Use for 'stock levels', 'inventory', 'how much stock', 'how many pieces'.",
    "get_stock_reserve": "Get stock reserve calculation — days of cover, safe withdrawal amount. Use for 'stock reserve', 'how much stock to keep', 'days of cover'.",
    "get_lost_sales_summary": "Get summary of lost sales this month — missed revenue, count. Use for 'lost sales', 'missed sales', 'out of stock losses'.",
    "get_margin_alerts": "Get alerts for categories below margin target. Use for 'margin alerts', 'which categories have low margin', 'margin problems'.",
    "get_customer_credit_top": "Get top customers with outstanding credit/urdhaar. Use for 'credit', 'urdhaar', 'who owes me', 'outstanding'.",
    "get_expenses_summary": "Get expense summary for a month — total, by category, budget vs actual. Use for 'expenses', 'how much did I spend', 'operating costs'.",
    "get_daily_stock": "Get daily stock report — opening, purchases, sales, closing for each category. Use for 'daily stock', 'stock movement', 'stock report'.",
    "get_shift_variances": "Get cash drawer variances by shift. Use for 'shift variance', 'cash shortage', 'drawer variance'.",
    "get_trend_signals": "Get trend alerts — sales trends, declining items, growing items. Use for 'trends', 'what's trending', 'sales trend'.",
    "get_owner_hub": "Get consolidated multi-branch dashboard — total sales across all branches, leaderboard. Use for 'all branches', 'consolidated', 'total business', 'owner hub'.",
    "get_branches": "Get list of registered branches. Use for 'branches', 'how many branches', 'list branches'.",
    "get_transfers": "Get inter-branch transfer challans. Use for 'transfers', 'challan', 'stock transfer'.",
    "web_search": "Search the web for real-time information — market prices, competitor pricing, product availability, industry news, weather, or any external information. Use for questions about current market conditions, competitor prices, or anything NOT in the local business database. Pass a search query string.",
}

TOOL_SCHEMAS = [
    {"type": "function", "function": {
        "name": n,
        "description": TOOL_DESCRIPTIONS.get(n, n.replace("_", " ")),
        "parameters": {"type": "object", "properties": {}} if n != "web_search" else {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query to look up on the web"}
            },
            "required": ["query"],
        },
    }}
    for n in READ_TOOLS
]

# ─── CONSTRAINED SQL (Phase 4) ──────────────────────────────────────────────

SQL_ALLOWLIST = {
    "bills", "bill_items", "bill_pages", "sales", "sale_items", "customers",
    "suppliers", "price_categories", "expenses", "expense_categories",
    "recurring_expenses", "cash_drawer", "shifts", "employees",
    "category_stock_state", "owner_withdrawals", "supplier_advances",
    "supplier_rates", "bank_accounts", "bank_transactions",
    "commission_rules", "commissions", "lost_sales", "closed_days", "seasons",
    "bundles", "bundle_items", "price_rules", "activity_log", "pending_actions",
    "ai_usage",
    # v8.0: Multi-branch tables (read-only via constrained SQL)
    "branch_config", "branches", "branch_summaries", "sync_outbox",
    "transfer_challans", "transfer_challan_items",
    "central_purchases", "central_purchase_items", "price_pushes",
    # v8.13.0+: Category ops + write-offs + capital injections
    "stock_writeoffs", "capital_injections",
    "stock_adjustments", "corrections", "ezi_pos_imports",
    "bill_intelligence", "audit_runs", "audit_findings",
    "customer_payments", "loyalty_redemptions", "quotations", "held_orders",
    "purchase_orders", "purchase_order_items", "pos_imports",
    "reorder_reminders", "trend_alerts", "custom_items", "item_discounts",
    "payment_methods",
}
# v8.13.1: tables that contain secrets/sessions — NEVER accessible via /api/agent/sql
# (These are also implicitly blocked by SQL_ALLOWLIST, but listed explicitly
# for defense-in-depth — if a future contributor adds them to SQL_ALLOWLIST
# by mistake, this list still blocks them.)
SQL_FORBIDDEN = {
    "settings",            # password_hash, crypto_salt, API keys (encrypted)
    "ai_providers",         # API keys (encrypted but recoverable)
    "sessions",            # active session tokens (cookie hijack)
    "ai_cache",             # cached LLM responses — may contain prompt data
    "pairing_codes",       # 6-digit pairing codes (manager-escalation)
    "devices",             # device token hashes
    "login_attempts",      # IP-based throttle data
    "branch_pairing_codes", # multi-branch pairing codes
    "employees",           # PIN hashes (bcrypt) — offline cracking target
}


def execute_constrained_sql(query: str) -> dict:
    """Execute a read-only SQL query with safety constraints.

    SECURITY (v8.13.1): Replaced the regex-based table detection with a
    strict ALLOWLIST approach. The old regex `\\bFROM\\s+(\\w+)|\\bJOIN\\s+(\\w+)`
    was bypassable via comma-join: `SELECT password_hash FROM bills, settings`
    matched only `bills`, allowing access to the forbidden `settings` table.

    The new approach: tokenize the query and reject ANY identifier that is
    not in the allowlist. Also rejects subqueries, UNION, and CTE references
    to forbidden tables.

    Constraints:
    - Read-only connection (mode=ro)
    - SELECT/WITH only (no INSERT/UPDATE/DELETE/DROP/etc.)
    - STRICT table allowlist (only explicitly-permitted tables)
    - LIMIT 50 injected if not present (down from 500 — limits secret exfil)
    - 10s timeout
    - Rejects subqueries, UNION-combined queries, and CTEs that touch forbidden tables
    """
    query = query.strip()
    if not query:
        return {"error": "Empty query"}
    # Check for forbidden keywords
    upper = query.upper()
    for kw in ["INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE",
               "ATTACH", "PRAGMA", "VACUUM", "REPLACE", "MERGE", "TRIGGER",
               "INDEX", "VIEW", "SAVEPOINT", "TRANSACTION", "COMMIT", "ROLLBACK"]:
        if re.search(rf'\b{kw}\b', upper):
            return {"error": f"Only SELECT queries are allowed (found {kw})"}
    if not upper.startswith("SELECT") and not upper.startswith("WITH"):
        return {"error": "Query must start with SELECT or WITH"}

    # v8.13.1: STRICT ALLOWLIST — extract ALL identifiers that follow
    # FROM, JOIN, INTO, or comma-separated table lists.
    #
    # Previous regex `([^\s]+)` stopped at whitespace, so `FROM bills, settings`
    # only captured `bills,` and missed `settings` → the comma-join bypass.
    #
    # New approach: capture the ENTIRE FROM/JOIN clause (up to the next SQL
    # keyword like WHERE/GROUP/ORDER/LIMIT/HAVING/UNION), then split on
    # commas and extract every identifier. Also scan for JOIN/INTO separately.
    identifiers_found = set()
    # Match FROM <table_list> — capture everything until a SQL clause keyword
    # or end of string. The table_list can be: table_a / table_a, table_b /
    # table_a AS alias, table_b / (subquery) AS alias
    from_pattern = re.compile(
        r'\bFROM\s+(.*?)(?=\b(?:WHERE|GROUP|ORDER|LIMIT|HAVING|UNION|EXCEPT|INTERSECT|JOIN|INNER|LEFT|RIGHT|OUTER|CROSS|NATURAL|ON|WINDOW|QUALIFY)\b|$)',
        re.IGNORECASE | re.DOTALL,
    )
    for m in from_pattern.finditer(query):
        chunk = m.group(1)
        # Split on commas to get individual table references
        for part in chunk.split(','):
            # Each part may be: "table_name" / "table_name AS alias" / "table_name alias" / "(subquery) AS alias"
            part = part.strip()
            if part.startswith('('):
                # Subquery — the FROM pattern above already catches the inner
                # FROM, so we don't need to extract the subquery's tables here
                # (the regex is global — it'll match the inner FROM separately)
                continue
            # Extract the first identifier (the table name) — strip alias keywords
            id_match = re.match(r'([A-Za-z_]\w*)', part)
            if id_match:
                identifiers_found.add(id_match.group(1).lower())
    # Also scan JOIN ... — the table name is right after JOIN/INNER JOIN/LEFT JOIN/etc.
    for m in re.finditer(r'\b(?:INNER\s+|LEFT\s+|RIGHT\s+|OUTER\s+|CROSS\s+|NATURAL\s+)?JOIN\s+(\w+)', query, re.IGNORECASE):
        identifiers_found.add(m.group(1).lower())
    # Also scan for INTO (defensive — should be blocked above, but defense in depth)
    for m in re.finditer(r'\bINTO\s+(\w+)', query, re.IGNORECASE):
        identifiers_found.add(m.group(1).lower())
    # Check every identifier against the ALLOWLIST (not the forbidden list)
    for tbl in identifiers_found:
        if tbl not in SQL_ALLOWLIST:
            return {"error": f"Access to table '{tbl}' is not permitted (strict allowlist)"}
        if tbl in SQL_FORBIDDEN:
            return {"error": f"Access to table '{tbl}' is forbidden"}
    # v8.13.1: Defense in depth — scan for ANY forbidden table name as a bare
    # identifier anywhere in the query (catches subquery aliases, CTEs, etc.
    # that the FROM-clause regex might miss). This is conservative and may
    # produce false positives if a column name matches a forbidden table name,
    # but the trade-off is acceptable for security.
    all_tokens = set(re.findall(r'\b([a-zA-Z_]\w*)\b', query.lower()))
    for forbidden_tbl in SQL_FORBIDDEN:
        if forbidden_tbl in all_tokens:
            return {"error": f"Access to table '{forbidden_tbl}' is forbidden"}
    # Inject LIMIT — v8.13.1: cap at 50 rows (down from 500) to limit secret exfiltration
    if "LIMIT" not in upper:
        query += " LIMIT 50"
    try:
        ro_path = f"file:{_db.DB_PATH}?mode=ro"
        ro_conn = sqlite3.connect(ro_path, uri=True, timeout=10)
        ro_conn.row_factory = sqlite3.Row
        cur = ro_conn.execute(query)
        rows = cur.fetchall()
        col_names = [d[0] for d in cur.description] if cur.description else []
        ro_conn.close()
        return {"columns": col_names, "rows": [dict(r) for r in rows], "row_count": len(rows)}
    except sqlite3.Error as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"Query failed: {e}"}


# ─── AGENT LOOP (Phase 3) ───────────────────────────────────────────────────

def _get_groq_config():
    """Find a configured Groq provider. Returns (api_key, model) or (None, None)."""
    from .db import get_setting
    try:
        from . import crypto as _crypto
        with conn() as c:
            row = c.execute(
                "SELECT api_key, model FROM ai_providers "
                "WHERE provider_type='groq' AND enabled=1 ORDER BY priority LIMIT 1"
            ).fetchone()
            if row and row["api_key"]:
                key = _crypto.decrypt_api_key(row["api_key"])
                model = row["model"] or "meta-llama/llama-4-scout-17b-16e-instruct"
                return key, model
    except Exception as _e:
        logger.warning("Silent exception in agent.py: %s", _e, exc_info=True)
    # Fallback to env var
    key = os.getenv("GROQ_KEY") or os.getenv("GROQ_API_KEY")
    model = "meta-llama/llama-4-scout-17b-16e-instruct"
    return (key, model) if key else (None, None)


def _call_groq_with_tools(question: str, messages: list, tools: list, groq_key: str, groq_model: str) -> dict:
    """Call Groq with tool definitions and return the response.

    Returns the full JSON response from Groq's /chat/completions endpoint.
    """
    import httpx
    r = httpx.post(
        "https://api.groq.com/openai/v1/chat/completions",
        json={
            "model": groq_model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "temperature": 0.3,
            "max_tokens": 1500,
        },
        headers={"Authorization": f"Bearer {groq_key}"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def _run_llm_agent(question: str, max_iterations: int = 6) -> dict:
    """Run the real LLM agent loop with tool calling.

    The LLM decides which tools to call based on the question + tool descriptions.
    We execute the tools locally and feed results back to the LLM for a natural-language answer.
    """
    groq_key, groq_model = _get_groq_config()
    if not groq_key:
        return {}  # signal fallback

    trace = []
    system_prompt = (
        "You are BillBook AI, a business assistant for Pakistani wholesale shopkeepers. "
        "You have access to real-time business data through tools. "
        "ALWAYS use tools to get actual data before answering — never guess numbers. "
        "When you have the data, give a concise, clear answer in 2-3 sentences. "
        "Use Rs for amounts. If multiple tools are needed, call them all. "
        "If the question doesn't need any tool (e.g. general advice), answer directly without tools.\n\n"

        "BUSINESS MODEL:\n"
        "This is a wholesale discount shop. Products are grouped into price categories:\n"
        "  - Category A (Budget): sell price Rs 250\n"
        "  - Category B (Standard): sell price Rs 500\n"
        "  - Category C (Premium): sell price Rs 750\n"
        "  - Category D (Luxury): sell price Rs 1000\n"
        "Plus bag categories (Bag Rs 10/20/30/50/60).\n\n"

        "HOW MARGINS ARE CALCULATED:\n"
        "  - Each product has a COST PRICE (what the shopkeeper paid the supplier) and a SELL PRICE (what the customer pays).\n"
        "  - Profit per unit = Sell Price - Cost Price\n"
        "  - Margin % = (Profit / Sell Price) × 100\n"
        "  - Total Profit = SUM(sell_price × qty) - SUM(cost_price × qty) for all non-refunded, non-voided sales\n"
        "  - Overall Margin = Total Profit / Total Sales × 100 (weighted by sales volume, NOT a simple average)\n"
        "  - The cost_price for each sale is captured at sale time from the running weighted-average cost engine.\n"
        "  - The running weighted-average cost changes when new supplier bills are confirmed (purchases).\n"
        "  - Per-item discounts (v8.8+) and order-level discounts both reduce revenue; loyalty point redemptions reduce revenue.\n"
        "  - Tax (FBR sales tax) may be configured inclusive or exclusive of the sell price — check the settings if relevant.\n\n"

        "HOW STOCK IS TRACKED:\n"
        "  - category_stock_state is the single source of truth for current stock.\n"
        "  - Stock = Purchases - Sales + Adjustments.\n"
        "  - Purchases come from confirmed supplier bills (bills.status='confirmed' AND bills.deleted_at IS NULL).\n"
        "  - Sales come from POS transactions (both native BillBook sales and imported POS sales).\n"
        "  - Sales with payment_status='refunded' or 'voided' do NOT reduce stock — only 'paid', 'credit', 'partial' do.\n"
        "  - avg_cost = current_value / current_qty (running weighted average, NOT last purchase price).\n"
        "  - If stock_state_dirty flag is set (e.g. after a crashed POS import), the next boot rebuilds stock_state from scratch.\n\n"

        "PAYMENT METHODS (v8.8+):\n"
        "  - Cash, Card, Online, Credit, Split.\n"
        "  - Online payments have sub-methods: Easypaisa, JazzCash, Raast QR, Bank Transfer.\n"
        "  - Split payments divide the total across cash + card + online.\n"
        "  - Refunds reverse ONLY the cash portion into the cash_drawer (split/card/online refunds don't touch the drawer).\n\n"

        "CASH POSITION + CAPITAL INJECTIONS (v8.12.1+):\n"
        "  - Cash in Drawer = SUM(cash_drawer.amount) — every sale adds +, every purchase adds −, every owner withdrawal adds −.\n"
        "  - Available for Withdrawal = Cash − Stock Replacement (this month COGS) − OpEx (this month) − Business Reserve (10% of GP).\n"
        "  - If 'Available for Withdrawal' is NEGATIVE, the most common cause on Day 1 is: the owner invested capital to buy initial stock (recorded as confirmed supplier bills = cash_drawer −) but never recorded the matching cash-in. Fix: Settings → Cash Buckets → 'Capital Injection' button → enter the initial investment amount + admin PIN. This credits cash_drawer by +amount and brings the withdrawal number back to non-negative.\n"
        "  - Capital injections are EQUITY, not revenue — they do NOT inflate sales, COGS, or gross profit. They only affect cash_in_drawer.\n"
        "  - Capital injection sources: owner_pocket (personal savings), partner (co-owner), bank_loan (loan injected), opening_balance (one-time Day-1 fix).\n\n"

        "DELETED-DATA SEMANTICS (v8.11+):\n"
        "  - Suppliers and customers use soft-delete (deleted_at timestamp). They disappear from lists, KPI tiles, and reports, but their historical bills/sales remain intact for audit.\n"
        "  - Sales have TWO soft-delete states: 'refunded' (returned) and 'voided' (admin-corrected mistake).\n"
        "  - Bills use a `deleted_at` timestamp + `version` integer column (optimistic concurrency control).\n"
        f"  - ALL KPI tiles and reports filter `deleted_at IS NULL` for suppliers/customers/bills, and `{db.VALID_SALE_FILTER_NO_ALIAS}` for sales.\n"
        "  - The POS Import sync detects deleted + modified sales by comparing Ezi POS backups to BillBook's database.\n\n"

        "HOW TO ANSWER SPECIFIC QUESTION TYPES:\n"
        "  - 'How is my margin calculated?' → call get_margins, explain: 'Your overall margin is X% because total sales Rs Y minus total COGS Rs Z = profit Rs W. This is weighted by sales volume.'\n"
        "  - 'Why is category A margin low?' → call get_margins, check if avg_cost is high relative to sell_price, explain the cost vs sell price.\n"
        "  - 'How much stock do I have?' → call get_inventory_state, report qty + value per category.\n"
        "  - 'How much profit did I make?' → call get_monthly_profit, report sales - COGS = gross profit.\n"
        "  - 'How much cash can I withdraw?' → call get_cash_buckets, report available_for_withdrawal.\n"
        "  - 'Who owes me money?' → call get_customer_credit_top, list customers with total_credit > 0 (soft-deleted customers are excluded).\n"
        "  - 'How do I record a refund / void?' → Refunds are processed from the POS History page (sale detail → Refund). Voids are admin-only from the sale detail page. Both are atomic and preserve audit trails.\n"
        "  - 'Why did my supplier count go down?' → Soft-deleted suppliers don't appear in counts. If the count seems wrong, suggest running Settings → Data Reconciliation → Repair.\n"
        "  - 'Why is my available-for-withdrawal negative?' → Most likely: the owner invested capital to buy initial stock (recorded as supplier bills = cash_drawer −) but never recorded the matching +amount. Fix: Billing → Cash Buckets → 'Capital Injection' button → record the initial investment with admin PIN.\n"
        "  - 'How do I record money I personally put into the business?' → Use the Capital Injection button on the Cash Buckets page. Pick a source (owner_pocket / partner / bank_loan / opening_balance) and enter the amount + admin PIN. The injection credits cash_drawer but does NOT count as revenue.\n\n"

        "IMPORTANT RULES:\n"
        "  1. ALWAYS cite the actual numbers from tool results — never round or approximate.\n"
        "  2. If a number seems wrong (e.g. negative stock), explain WHY (usually: sold before purchase bill was entered, OR stock_state_dirty is set and needs a rebuild on next boot).\n"
        "  3. If asked about COGS, explain it = cost_price × qty captured at sale time.\n"
        "  4. If asked about a specific category, use the category code (A/B/C/D) and sell price.\n"
        "  5. Use 'Rs' prefix for all monetary amounts (Pakistani Rupees).\n"
        "  6. If asked about deleted/missing data (e.g. 'where did supplier X go?'), explain the soft-delete model — they may have been deleted but their bills remain in the audit log.\n"
        "  7. If asked about the AI Auditor or safe withdrawal, mention the formula: Safe = Cash - Stock Replacement - OpEx - Business Reserve."
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": question},
    ]

    tool_results = {}

    for iteration in range(max_iterations):
        try:
            resp = _call_groq_with_tools(question, messages, TOOL_SCHEMAS, groq_key, groq_model)
        except Exception as e:
            logger.error("Groq API call failed: %s", e)
            return {}  # signal fallback to heuristic

        choice = resp.get("choices", [{}])[0]
        msg = choice.get("message", {})

        # If the LLM wants to call tools, execute them
        tool_calls = msg.get("tool_calls", [])
        if not tool_calls:
            # No tool calls — the LLM is giving us the final answer
            answer = msg.get("content", "").strip()
            if not answer:
                answer = "I couldn't find an answer for that. Try rephrasing your question."
            followups = _suggest_followups(question, tool_results)
            return {"answer": answer, "tool_trace": trace, "suggested_followups": followups}

        # Execute each tool call
        messages.append(msg)  # add assistant message with tool_calls

        for tc in tool_calls:
            fn = tc.get("function", {})
            tool_name = fn.get("name", "")
            if tool_name not in READ_TOOLS:
                trace.append({"step": "tool_result", "tool": tool_name, "status": "error",
                              "error": f"Unknown tool: {tool_name}"})
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": f"Error: unknown tool '{tool_name}'",
                })
                continue

            trace.append({"step": "tool_call", "tool": tool_name, "status": "running"})
            try:
                # v8.4: Parse function arguments from the LLM's tool call
                # (e.g. web_search needs a "query" parameter)
                args_str = fn.get("arguments", "{}")
                try:
                    tool_params = json.loads(args_str) if isinstance(args_str, str) else (args_str or {})
                except json.JSONDecodeError:
                    tool_params = {}
                result = READ_TOOLS[tool_name](tool_params)
                tool_results[tool_name] = result
                summary = _summarize_tool_result(tool_name, result)
                trace.append({"step": "tool_result", "tool": tool_name, "status": "ok",
                              "summary": summary})
                # Feed the full result back to the LLM
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": json.dumps(result, default=str),
                })
            except Exception as e:
                trace.append({"step": "tool_result", "tool": tool_name, "status": "error",
                              "error": str(e)})
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": f"Error: {e}",
                })

        # Loop continues — LLM will see the tool results and either call more tools or give final answer

    # Exhausted max iterations — build answer from what we have
    answer = _build_answer_from_tools(question, tool_results)
    followups = _suggest_followups(question, tool_results)
    return {"answer": answer, "tool_trace": trace, "suggested_followups": followups}


def run_agent(question: str, max_iterations: int = 6) -> dict:
    """Run the agent loop for a user question.

    v8.4: When a Groq API key is configured, uses real LLM tool-calling —
    the LLM decides which tools to call and composes a natural-language answer.
    Falls back to heuristic keyword matching when no key is available.

    Returns {answer, tool_trace, suggested_followups}
    """
    from .ai_router import is_ai_disabled

    trace = []
    answer = ""
    followups = []

    if is_ai_disabled():
        trace.append({"step": "kill_switch", "status": "blocked", "message": "AI is disabled"})
        return {"answer": "AI is currently disabled. Enable it in Settings to use the assistant.",
                "tool_trace": trace, "suggested_followups": []}

    # Greeting / small-talk detection — don't call any tools for these.
    greeting = _detect_greeting(question)
    if greeting:
        return {
            "answer": greeting["answer"],
            "tool_trace": [],
            "suggested_followups": greeting["followups"],
        }

    # v8.4: Try real LLM tool-calling first (when Groq key is configured)
    try:
        llm_result = _run_llm_agent(question, max_iterations)
        if llm_result and llm_result.get("answer"):
            return llm_result
    except Exception as e:
        logger.warning("LLM agent failed, falling back to heuristic: %s", e)

    # ── HEURISTIC FALLBACK (when no Groq key or LLM call failed) ──
    # This is offline-safe and uses keyword matching to pick tools.
    # v8.5.5: enhanced to also answer "how is my margin calculated" type questions
    q_lower = question.lower()

    tools_to_call = []
    if "margin" in q_lower and ("month" in q_lower or "actual" in q_lower or "overall" in q_lower):
        tools_to_call.append("get_margins")
    # v8.5.5: also call get_margins for "how is margin calculated" / "category A margin" / "why is margin low"
    if ("margin" in q_lower and ("how" in q_lower or "calculat" in q_lower or "category" in q_lower or "why" in q_lower or "low" in q_lower or "a margin" in q_lower or "b margin" in q_lower or "c margin" in q_lower or "d margin" in q_lower)):
        tools_to_call.append("get_margins")
    if "cogs" in q_lower or "cost of goods" in q_lower:
        tools_to_call.append("get_margins")
        if "month" in q_lower or "this month" in q_lower:
            tools_to_call.append("get_monthly_profit")
    if "monthly" in q_lower or "this month" in q_lower or "profit" in q_lower:
        if "cogs" in q_lower or "bridge" in q_lower or "profit" in q_lower or "earn" in q_lower or "how much" in q_lower:
            tools_to_call.append("get_monthly_profit")
    if "ytd" in q_lower or "year" in q_lower or "cumulative" in q_lower:
        tools_to_call.append("get_ytd")
    if "cash" in q_lower and ("bucket" in q_lower or "withdraw" in q_lower or "reserve" in q_lower):
        tools_to_call.append("get_cash_buckets")
    if "break" in q_lower and "even" in q_lower:
        tools_to_call.append("get_break_even")
    if "stock" in q_lower and ("level" in q_lower or "inventory" in q_lower or "how much" in q_lower):
        tools_to_call.append("get_inventory_state")
    if "lost" in q_lower and "sale" in q_lower:
        tools_to_call.append("get_lost_sales_summary")
    if "alert" in q_lower or "margin" in q_lower and "protect" in q_lower:
        tools_to_call.append("get_margin_alerts")
    if "urdhaar" in q_lower or "credit" in q_lower or "outstanding" in q_lower:
        tools_to_call.append("get_customer_credit_top")
    if "expense" in q_lower:
        tools_to_call.append("get_expenses_summary")
    if "shift" in q_lower and "variance" in q_lower:
        tools_to_call.append("get_shift_variances")
    if "trend" in q_lower:
        tools_to_call.append("get_trend_signals")
    if "branch" in q_lower or "all branches" in q_lower or "every branch" in q_lower:
        tools_to_call.append("get_branches")
    if "consolidated" in q_lower or "owner hub" in q_lower or "all shops" in q_lower or "across branches" in q_lower or "total business" in q_lower:
        tools_to_call.append("get_owner_hub")
    if "transfer" in q_lower or "challan" in q_lower:
        tools_to_call.append("get_transfers")
    # v8.4: Web search for external/market questions
    if any(kw in q_lower for kw in ["market price", "competitor", "online", "google", "search web",
                                     "web search", "latest", "current price", "news", "weather",
                                     "trend in market", "wholesale rate", "market rate"]):
        tools_to_call.append("web_search")

    # If no specific tool matched, use general BI
    if not tools_to_call:
        tools_to_call.append("get_margins")
        tools_to_call.append("get_monthly_profit")

    tools_to_call = tools_to_call[:max_iterations]

    tool_results = {}
    for tool_name in tools_to_call:
        if tool_name in READ_TOOLS:
            trace.append({"step": "tool_call", "tool": tool_name, "status": "running"})
            try:
                # v8.4: web_search needs the query param — use the original question
                params = {}
                if tool_name == "web_search":
                    params = {"query": question}
                result = READ_TOOLS[tool_name](params)
                tool_results[tool_name] = result
                trace.append({"step": "tool_result", "tool": tool_name, "status": "ok",
                              "summary": _summarize_tool_result(tool_name, result)})
            except Exception as e:
                trace.append({"step": "tool_result", "tool": tool_name, "status": "error", "error": str(e)})

    answer = _build_answer_from_tools(question, tool_results)
    followups = _suggest_followups(question, tool_results)

    return {"answer": answer, "tool_trace": trace, "suggested_followups": followups}


def _summarize_tool_result(tool_name: str, result: dict) -> str:
    if tool_name == "get_margins":
        return f"Actual Overall Margin: {result.get('actual_overall_margin', 'N/A')}%"
    elif tool_name == "get_monthly_profit":
        return f"Sales: Rs {result.get('sales', 0):,.0f}, COGS: Rs {result.get('cogs', 0):,.0f}, GP: Rs {result.get('gross_profit', 0):,.0f}"
    elif tool_name == "get_ytd":
        return f"YTD Sales: Rs {result.get('ytd_sales', 0):,.0f}, YTD GP: Rs {result.get('ytd_gross_profit', 0):,.0f}, Margin: {result.get('ytd_margin', 0)}%"
    elif tool_name == "get_cash_buckets":
        return f"Cash: Rs {result.get('cash_in_drawer', 0):,.0f}, Available: Rs {result.get('available_for_withdrawal', 0):,.0f}"
    elif tool_name == "get_inventory_state":
        items = result.get("items", [])
        total = sum(i.get("stock_value", 0) for i in items)
        return f"{len(items)} categories, total stock value: Rs {total:,.0f}"
    elif tool_name == "get_break_even":
        return f"Daily target: Rs {result.get('daily_target', 0):,.0f}, So far: Rs {result.get('daily_so_far', 0):,.0f}"
    elif tool_name == "get_owner_hub":
        c = result.get("consolidated", {})
        return f"Consolidated sales: Rs {c.get('sales', 0):,.0f}, GP: Rs {c.get('gross_profit', 0):,.0f}, {result.get('branch_count', 0)} branches"
    elif tool_name == "get_branches":
        return f"{result.get('count', 0)} branches registered"
    elif tool_name == "get_transfers":
        return f"{result.get('count', 0)} transfer challans"
    elif tool_name == "web_search":
        count = result.get("count", 0)
        if count > 0:
            first = result["results"][0]
            return f"Found {count} results — top: {first.get('title', '?')[:60]}"
        return result.get("message", "No web results found")
    else:
        return f"Retrieved data from {tool_name}"


def _build_answer(question: str, results: dict) -> str:
    return _build_answer_from_tools(question, results)


def _build_answer_from_tools(question: str, results: dict) -> str:
    parts = []
    q_lower = question.lower()
    if "get_margins" in results:
        m = results["get_margins"]
        total_sales = m.get('total_sales', 0)
        total_cogs = m.get('total_cogs', 0)
        total_gp = m.get('total_gross_profit', 0)
        actual_margin = m.get('actual_overall_margin', 0)
        cat_avg = m.get('category_average_margin', 0)

        # v8.5.5: If the user asked "how is my margin calculated", give a detailed breakdown
        if "how" in q_lower and ("margin" in q_lower or "calculat" in q_lower):
            parts.append(
                f"Your overall margin is {actual_margin}%. Here's how it's calculated:\n"
                f"  Total Sales (all non-refunded sales): Rs {total_sales:,.0f}\n"
                f"  Total COGS (cost_price × qty for each sale): Rs {total_cogs:,.0f}\n"
                f"  Gross Profit = Sales - COGS = Rs {total_gp:,.0f}\n"
                f"  Overall Margin = Profit ÷ Sales × 100 = {actual_margin}%\n"
                f"This is a sales-weighted average (NOT a simple average of category margins).\n"
                f"Per-category breakdown:"
            )
            for cat in m.get('categories', []):
                parts.append(
                    f"  • Category {cat.get('code', '?')} ({cat.get('name', '?')}): "
                    f"sell Rs {cat.get('sell_price', 0)}, cost Rs {cat.get('avg_cost', 0)}, "
                    f"margin {cat.get('margin_pct', 0)}%"
                )
        elif "category" in q_lower or "why" in q_lower or "low" in q_lower:
            # User asked about a specific category or why margin is low
            parts.append(f"Your Actual Overall Gross Margin is {actual_margin}%.")
            for cat in m.get('categories', []):
                parts.append(
                    f"  Category {cat.get('code', '?')} ({cat.get('name', '?')}): "
                    f"sell Rs {cat.get('sell_price', 0)}, avg cost Rs {cat.get('avg_cost', 0)}, "
                    f"margin {cat.get('margin_pct', 0)}%"
                )
            parts.append(
                f"Total Sales: Rs {total_sales:,.0f}, COGS: Rs {total_cogs:,.0f}, "
                f"Gross Profit: Rs {total_gp:,.0f}. "
                f"The margin is weighted by sales volume — categories that sell more "
                f"have a bigger impact on the overall margin."
            )
        else:
            parts.append(f"Your Actual Overall Gross Margin is {actual_margin}% "
                         f"(Category Average: {cat_avg}%). "
                         f"Total Sales: Rs {total_sales:,.0f}, "
                         f"Total Gross Profit: Rs {total_gp:,.0f}.")
    if "get_monthly_profit" in results:
        p = results["get_monthly_profit"]
        parts.append(f"This month: Sales Rs {p.get('sales', 0):,.0f}, "
                     f"COGS Rs {p.get('cogs', 0):,.0f}, "
                     f"Gross Profit Rs {p.get('gross_profit', 0):,.0f}, "
                     f"Operating Profit Rs {p.get('operating_profit', 0):,.0f}.")
    if "get_ytd" in results:
        y = results["get_ytd"]
        parts.append(f"YTD: Sales Rs {y.get('ytd_sales', 0):,.0f}, "
                     f"GP Rs {y.get('ytd_gross_profit', 0):,.0f}, "
                     f"Margin {y.get('ytd_margin', 0)}%.")
    if "get_cash_buckets" in results:
        c = results["get_cash_buckets"]
        parts.append(f"Cash in drawer: Rs {c.get('cash_in_drawer', 0):,.0f}. "
                     f"Available for withdrawal: Rs {c.get('available_for_withdrawal', 0):,.0f}.")
    if "get_break_even" in results:
        b = results["get_break_even"]
        parts.append(f"Break-even: must sell Rs {b.get('daily_target', 0):,.0f}/day "
                     f"(Rs {b.get('daily_so_far', 0):,.0f} so far today).")
    if "get_inventory_state" in results:
        items = results["get_inventory_state"].get("items", [])
        total = sum(i.get("stock_value", 0) for i in items)
        parts.append(f"Current stock: {len(items)} categories, total value Rs {total:,.0f}.")
    if "get_lost_sales_summary" in results:
        l = results["get_lost_sales_summary"]
        parts.append(f"Missed revenue this month: Rs {l.get('total_est_revenue', 0):,.0f} "
                     f"across {l.get('count', 0)} lost sales.")
    if "get_margin_alerts" in results:
        alerts = results["get_margin_alerts"].get("alerts", [])
        if alerts:
            parts.append(f"{len(alerts)} categories below margin target. "
                         f"First: {alerts[0]['code']} at {alerts[0]['margin_pct']}% "
                         f"(suggested price: Rs {alerts[0]['suggested_price']}).")
    if "get_customer_credit_top" in results:
        custs = results["get_customer_credit_top"].get("customers", [])
        if custs:
            parts.append(f"{len(custs)} customers with outstanding credit. "
                         f"Top: {custs[0]['name']} owes Rs {custs[0]['total_credit']:,.0f}.")
    # v8.0: Multi-branch answers
    if "get_owner_hub" in results:
        hub = results["get_owner_hub"]
        c = hub.get("consolidated", {})
        branch_count = hub.get("branch_count", 0)
        if branch_count > 0:
            parts.append(f"Across all {branch_count} branches: consolidated sales Rs {c.get('sales', 0):,.0f}, "
                         f"gross profit Rs {c.get('gross_profit', 0):,.0f}, "
                         f"cash in drawer Rs {c.get('cash_in_drawer', 0):,.0f}.")
            leaderboard = hub.get("leaderboard", [])
            if leaderboard:
                top = leaderboard[0]
                parts.append(f"Top branch: {top['name']} with Rs {top.get('sales', 0):,.0f} in sales.")
            stale = [b for b in hub.get("branches", []) if b.get("stale")]
            if stale:
                parts.append(f"⚠️ {len(stale)} branch(es) haven't synced in 24h: {', '.join(b['name'] for b in stale)}.")
        else:
            parts.append("No branches registered yet. This instance is running in single-shop mode.")
    if "get_branches" in results:
        branches = results["get_branches"].get("branches", [])
        if branches:
            parts.append(f"You have {len(branches)} registered branch(es): " +
                         ", ".join(f"{b['name']} ({b['branch_id']})" for b in branches) + ".")
        else:
            parts.append("No branches registered. This instance is in single-shop mode.")
    if "get_transfers" in results:
        transfers = results["get_transfers"].get("transfers", [])
        if transfers:
            in_transit = [t for t in transfers if t["status"] == "in_transit"]
            parts.append(f"{len(transfers)} transfer challan(s) total, {len(in_transit)} in transit.")
        else:
            parts.append("No transfer challans yet.")
    # v8.4: Web search results
    if "web_search" in results:
        ws = results["web_search"]
        web_results = ws.get("results", [])
        if web_results:
            parts.append(f"Here are {len(web_results)} web results for '{ws.get('query', '')}':")
            for i, r in enumerate(web_results[:3], 1):
                parts.append(f"{i}. {r.get('title', '?')} — {r.get('snippet', '')[:150]} ({r.get('url', '')})")
        elif ws.get("message"):
            parts.append(ws["message"])
        else:
            parts.append("No web results found.")
    return " ".join(parts) if parts else "I couldn't find relevant data for that question."


def _suggest_followups(question: str, results: dict) -> list:
    followups = []
    if "get_margins" in results:
        followups.append("What is my break-even daily target?")
    if "get_monthly_profit" in results:
        followups.append("How does this compare to last month?")
        followups.append("What is my YTD margin?")
    if "get_cash_buckets" in results:
        followups.append("How much can I safely withdraw?")
    if "get_inventory_state" in results:
        followups.append("Which categories have low stock?")
    # v8.0: Multi-branch followups
    if "get_owner_hub" in results:
        followups.append("Which branches haven't synced recently?")
        followups.append("Show me recent transfers")
    if "get_branches" in results and results.get("get_branches", {}).get("count", 0) > 0:
        followups.append("What is my consolidated sales across all branches?")
    if not followups:
        followups = ["What is my actual overall margin?", "How much cash can I withdraw?"]
    return followups[:3]


# ─── GREETING / SMALL-TALK DETECTION ────────────────────────────────────────
# When the user just says "hey", "hello", "salam", etc., we should NOT call
# any business-data tools. Return a friendly greeting + starter suggestions.

_GREETING_PATTERNS = [
    # English greetings
    r"^\s*(hey|hello|hi|hy|hii|hiya|yo|sup|howdy|greetings)\s*[!.?]*\s*$",
    # Time-of-day greetings
    r"^\s*(good\s*(morning|afternoon|evening|night))\s*[!.?]*\s*$",
    # How are you
    r"^\s*(how\s*are\s*you|how\s*are\s*ya|how\'?s\s*it\s*going|how\'?s\s*everything|whats\s*up|what\'?s\s*up|wassup|what\s*are\s*you\s*doing)\s*[!.?]*\s*$",
    # Thank you
    r"^\s*(thanks|thank\s*you|thx|ty|cool|nice|great|awesome|ok|okay|k|got\s*it|understood|roger)\s*[!.?]*\s*$",
    # Urdu/Roman-Urdu greetings
    r"^\s*(salam|salaam|assalam|assalamu|aslam|aoa|asa|adaab|adaab\*?z)\s*[!.?]*\s*$",
    r"^\s*(assalamu\s*alaikum|assalam\s*o\s*alaikum|assalam\s*alaikum|salam\s*alaikum)\s*[!.?]*\s*$",
    # Urdu small-talk
    r"^\s*(kaise\s*ho|kya\s*haal\s*hai|kya\s*hal\s*hai|kya\s*ho\s*raha\s*hai|kya\s*chal\s*raha\s*hai)\s*[!.?]*\s*$",
    # Bye
    r"^\s*(bye|goodbye|see\s*you|see\s*ya|cya|tata|khuda\s*hafiz|allah\s*hafiz)\s*[!.?]*\s*$",
    # Who/what are you
    r"^\s*(who\s*are\s*you|what\s*are\s*you|what\s*can\s*you\s*do|what\s*do\s*you\s*do|help|help\s*me)\s*[!.?]*\s*$",
]

_GREETING_REGEX = re.compile("|".join(_GREETING_PATTERNS), re.IGNORECASE)


def _detect_greeting(question: str) -> dict | None:
    """Return a friendly greeting reply if the question is small-talk, else None."""
    q = question.strip()
    if not q or len(q) > 80:
        return None
    if not _GREETING_REGEX.match(q):
        return None

    ql = q.lower()
    # Time-of-day aware greeting
    import datetime as _dt
    hour = _dt.datetime.now().hour
    if hour < 12:
        tod = "Good morning"
    elif hour < 17:
        tod = "Good afternoon"
    elif hour < 21:
        tod = "Good evening"
    else:
        tod = "Hello"

    # Custom replies for specific intents
    if re.search(r"\b(thanks|thank\s*you|thx|ty|cool|nice|great|awesome|got\s*it|understood|roger)\b", ql):
        return {
            "answer": "You're welcome! Anything else you'd like to know about your business?",
            "followups": [
                "What is my actual overall margin?",
                "How much cash can I safely withdraw?",
                "Which customers have outstanding credit?",
            ],
        }
    if re.search(r"\b(bye|goodbye|see\s*you|cya|tata|khuda\s*hafiz|allah\s*hafiz)\b", ql):
        return {
            "answer": "Goodbye! I'll be here whenever you need me.",
            "followups": [],
        }
    if re.search(r"\b(who\s*are\s*you|what\s*are\s*you|what\s*can\s*you\s*do|what\s*do\s*you\s*do|help)\b", ql):
        return {
            "answer": (
                "I'm your BillBook AI Assistant. I can answer questions about your sales, "
                "profit, margins, stock, expenses, customers, and cash position — my numbers "
                "always match your reports exactly because I read from the same data tools. "
                "Try one of the suggestions below to get started."
            ),
            "followups": [
                "What is my actual overall margin?",
                "How much cash can I safely withdraw?",
                "Which customers have outstanding credit?",
            ],
        }
    if re.search(r"\b(how\s*are\s*you|how\'?s\s*it\s*going|kaise\s*ho|kya\s*haal)\b", ql):
        return {
            "answer": (
                f"{tod}! I'm ready to help. Ask me anything about your business — "
                "sales, profit, margins, stock, expenses, or cash position."
            ),
            "followups": [
                "What is my actual overall margin?",
                "How much cash can I safely withdraw?",
                "What is my break-even daily target?",
            ],
        }
    # Default greeting reply (covers hey, hello, hi, salam, etc.)
    return {
        "answer": (
            f"{tod}! I'm your BillBook AI Assistant. "
            "Ask me about your sales, profit, margins, stock, expenses, or cash. "
            "My numbers always match your reports exactly."
        ),
        "followups": [
            "What is my actual overall margin?",
            "How much cash can I safely withdraw?",
            "Which customers have outstanding credit?",
        ],
    }
