"""v7.0 Phase 2 — AI Router: single entry point for all AI calls.

Enforces: cache → routing → budget → logging. All AI features MUST go
through ai_call(). No direct provider calls anywhere else after this phase.

Cache TTLs: BI answers 15min, narratives/briefs 24h, trends 6h, extraction ∞.
Budget guard: max_ai_calls_per_day per provider (Groq 500, Gemini 100).
Degradation: over budget → cached/heuristic fallback with badge, never crash.
"""
import hashlib, json, logging, time
from datetime import datetime
from .db import conn, get_setting

logger = logging.getLogger(__name__)

# TTL in seconds per task type
TTL = {"bi": 900, "narrative": 86400, "trends": 21600, "extraction": float('inf')}


def _cache_key(task: str, params: dict) -> str:
    raw = task + json.dumps(params, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def _get_cached(key: str, ttl: float) -> dict | None:
    if ttl == float('inf'):
        with conn() as c:
            row = c.execute("SELECT * FROM ai_cache WHERE key=?", (key,)).fetchone()
    else:
        cutoff = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        from datetime import timedelta
        cutoff = (datetime.now() - timedelta(seconds=ttl)).strftime("%Y-%m-%d %H:%M:%S")
        with conn() as c:
            row = c.execute(
                "SELECT * FROM ai_cache WHERE key=? AND created_at > ?", (key, cutoff)
            ).fetchone()
    return dict(row) if row else None


def _set_cached(key: str, task: str, response: str, provider: str, tokens_in: int = 0, tokens_out: int = 0):
    with conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO ai_cache(key, task, response_json, provider, tokens_in, tokens_out) "
            "VALUES(?,?,?,?,?,?)",
            (key, task, response, provider, tokens_in, tokens_out),
        )


def _log_usage(task: str, provider: str, model: str, tokens_in: int, tokens_out: int,
               cached: bool, duration_ms: int):
    with conn() as c:
        c.execute(
            "INSERT INTO ai_usage(task, provider, model, tokens_in, tokens_out, cached, duration_ms) "
            "VALUES(?,?,?,?,?,?,?)",
            (task, provider, model, tokens_in, tokens_out, 1 if cached else 0, duration_ms),
        )


def _check_budget(provider: str) -> bool:
    """Returns True if within daily budget, False if exhausted."""
    key = f"max_ai_calls_per_day_{provider}"
    limit = int(get_setting(key, "500" if provider == "groq" else "100") or "500")
    today = datetime.now().strftime("%Y-%m-%d")
    with conn() as c:
        row = c.execute(
            "SELECT COUNT(*) AS n FROM ai_usage WHERE provider=? AND cached=0 AND date(created_at)=?",
            (provider, today),
        ).fetchone()
    return (row["n"] if row else 0) < limit


def is_ai_disabled() -> bool:
    """Check the global AI kill switch."""
    with conn() as c:
        row = c.execute(
            "SELECT enabled FROM automation_config WHERE key='ai_kill_switch'"
        ).fetchone()
    return bool(row and row["enabled"])


def ai_call(task: str, params: dict, provider_hint: str = "groq",
            ttl_key: str = "bi", execute_fn=None) -> dict:
    """Single entry point for all AI calls.

    Args:
        task: human-readable task name (e.g., "bi_chat", "trends_synthesis")
        params: dict of parameters for the AI call
        provider_hint: "groq" for text/agent, "gemini" for vision
        ttl_key: cache TTL key ("bi", "narrative", "trends", "extraction")
        execute_fn: callable that makes the actual API call. Called only on cache miss.

    Returns:
        {"response": str, "provider": str, "cached": bool, "stale": bool, "disabled": bool}
    """
    # Check kill switch
    if is_ai_disabled():
        logger.info("AI kill switch active — returning heuristic-only response")
        return {"response": "", "provider": "none", "cached": False, "stale": False, "disabled": True}

    # Check cache
    key = _cache_key(task, params)
    ttl = TTL.get(ttl_key, 900)
    cached = _get_cached(key, ttl)
    if cached:
        _log_usage(task, cached["provider"] or provider_hint, "", 0, 0, True, 0)
        return {"response": cached["response_json"], "provider": cached["provider"],
                "cached": True, "stale": False, "disabled": False}

    # Check budget
    if not _check_budget(provider_hint):
        logger.warning("AI budget exhausted for %s — returning stale cache or empty", provider_hint)
        # Try returning stale cache (ignore TTL)
        stale = _get_cached(key, float('inf'))
        if stale:
            return {"response": stale["response_json"], "provider": stale["provider"],
                    "cached": True, "stale": True, "disabled": False}
        return {"response": "", "provider": "none", "cached": False, "stale": False, "disabled": False,
                "budget_exhausted": True}

    # Execute the actual AI call
    if execute_fn is None:
        return {"response": "", "provider": "none", "cached": False, "stale": False, "disabled": False,
                "error": "No execute_fn provided"}

    start = time.time()
    try:
        result = execute_fn()
        duration_ms = int((time.time() - start) * 1000)
        response = result.get("response", "")
        provider = result.get("provider", provider_hint)
        model = result.get("model", "")
        tokens_in = result.get("tokens_in", 0)
        tokens_out = result.get("tokens_out", 0)

        # Cache the result
        _set_cached(key, task, response, provider, tokens_in, tokens_out)
        # Log usage
        _log_usage(task, provider, model, tokens_in, tokens_out, False, duration_ms)

        return {"response": response, "provider": provider, "cached": False,
                "stale": False, "disabled": False}
    except Exception as e:
        logger.error("AI call failed: %s", e)
        duration_ms = int((time.time() - start) * 1000)
        _log_usage(task, provider_hint, "", 0, 0, False, duration_ms)
        # Try stale cache on error
        stale = _get_cached(key, float('inf'))
        if stale:
            return {"response": stale["response_json"], "provider": stale["provider"],
                    "cached": True, "stale": True, "disabled": False, "error": str(e)}
        return {"response": "", "provider": "none", "cached": False, "stale": False,
                "disabled": False, "error": str(e)}


def get_ai_usage_summary() -> dict:
    """Summary of AI usage for the dashboard."""
    today = datetime.now().strftime("%Y-%m-%d")
    with conn() as c:
        today_calls = c.execute(
            "SELECT provider, COUNT(*) AS n, SUM(CASE WHEN cached=0 THEN 1 ELSE 0 END) AS api_calls, "
            "SUM(CASE WHEN cached=1 THEN 1 ELSE 0 END) AS cache_hits, "
            "COALESCE(SUM(tokens_in + tokens_out), 0) AS tokens "
            "FROM ai_usage WHERE date(created_at)=? GROUP BY provider",
            (today,),
        ).fetchall()
        total_cached = c.execute(
            "SELECT COUNT(*) AS n FROM ai_cache"
        ).fetchone()["n"]
    groq_limit = int(get_setting("max_ai_calls_per_day_groq", "500") or "500")
    gemini_limit = int(get_setting("max_ai_calls_per_day_gemini", "100") or "100")
    providers = {}
    for r in today_calls:
        limit = groq_limit if r["provider"] == "groq" else gemini_limit
        providers[r["provider"]] = {
            "calls": r["n"],
            "api_calls": r["api_calls"],
            "cache_hits": r["cache_hits"],
            "tokens": r["tokens"],
            "budget_limit": limit,
            "budget_remaining": max(0, limit - r["api_calls"]),
        }
    return {"date": today, "providers": providers, "total_cached_entries": total_cached}


def get_ai_usage_14d() -> list:
    """Per-day usage for the last 14 days. Returns [{date, calls, api_calls, cache_hits, tokens}]."""
    from datetime import timedelta
    cutoff = (datetime.now() - timedelta(days=13)).strftime("%Y-%m-%d")
    with conn() as c:
        rows = c.execute(
            "SELECT date(created_at) AS d, COUNT(*) AS calls, "
            "SUM(CASE WHEN cached=0 THEN 1 ELSE 0 END) AS api_calls, "
            "SUM(CASE WHEN cached=1 THEN 1 ELSE 0 END) AS cache_hits, "
            "COALESCE(SUM(tokens_in + tokens_out), 0) AS tokens "
            "FROM ai_usage WHERE date(created_at)>=? GROUP BY date(created_at) "
            "ORDER BY d ASC", (cutoff,)).fetchall()
    # Fill missing days with zeros
    by_date = {r["d"]: dict(r) for r in rows}
    out = []
    for i in range(14):
        d = (datetime.now() - timedelta(days=13 - i)).strftime("%Y-%m-%d")
        if d in by_date:
            out.append(by_date[d])
        else:
            out.append({"d": d, "calls": 0, "api_calls": 0, "cache_hits": 0, "tokens": 0})
    return out


def get_recent_failures(limit: int = 20) -> list:
    """Recent AI calls that errored or hit budget exhaustion.

    The ai_usage table doesn't have an error column — we treat duration_ms=0
    with cached=0 and tokens=0 as a 'failed' call (no output produced).
    """
    with conn() as c:
        rows = c.execute(
            "SELECT id, task, provider, model, tokens_in, tokens_out, cached, duration_ms, created_at "
            "FROM ai_usage WHERE cached=0 AND COALESCE(tokens_in,0)=0 AND COALESCE(tokens_out,0)=0 "
            "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def clear_ai_cache() -> int:
    """Wipe the ai_cache table. Returns the number of rows deleted."""
    with conn() as c:
        cur = c.execute("DELETE FROM ai_cache")
        return cur.rowcount


# TTL info for the dashboard legend (mirror of TTL dict at top of file)
def get_ttl_legend() -> list:
    return [
        {"key": "bi", "label": "BI answers", "seconds": TTL["bi"], "human": "15 minutes"},
        {"key": "narrative", "label": "Narratives & briefs", "seconds": TTL["narrative"], "human": "24 hours"},
        {"key": "trends", "label": "Trends synthesis", "seconds": TTL["trends"], "human": "6 hours"},
        {"key": "extraction", "label": "Bill extraction", "seconds": None, "human": "Forever (no expiry)"},
    ]
