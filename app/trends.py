"""Market trend advisor + reorder reminders + seasonal alerts + dead stock clearance.
Fetches Google Trends data for Pakistan, uses Groq AI to analyze relevance
to the shop's inventory, and generates actionable suggestions.
Also generates reorder reminders based on purchase history (no internet needed).
"""
import logging
import json
import os
from datetime import datetime, timedelta
from .db import conn
from .validate import pieces, normalize_name

logger = logging.getLogger(__name__)


# ---------- Reorder Reminders (no internet needed) ----------

def generate_reorder_reminders() -> list:
    """Analyze purchase history and generate reorder reminders.
    
    For items purchased ≥3 times, computes average gap between purchases.
    If it's been longer than 1.2x the average gap, suggests a reorder.
    Also factors in seasonal patterns and dead stock.
    """
    from collections import defaultdict
    
    with conn() as c:
        rows = c.execute(
            "SELECT bi.raw, bi.qty, bi.unit, bi.price, b.supplier_name, b.bill_date "
            "FROM bill_items bi "
            "JOIN bills b ON bi.bill_id = b.id "
            "WHERE b.status='confirmed' AND b.deleted_at IS NULL "
            "AND bi.raw IS NOT NULL AND TRIM(bi.raw) <> '' "
            "AND b.bill_date IS NOT NULL "
            "ORDER BY bi.raw, b.bill_date"
        ).fetchall()
    
    by_item = defaultdict(list)
    for r in rows:
        key = normalize_name(r["raw"])
        if key:
            by_item[key].append({
                "name": r["raw"],
                "supplier": r["supplier_name"],
                "date": r["bill_date"][:10] if r["bill_date"] else None,
                "qty": pieces(r["qty"], r["unit"]),
                "price": r["price"] or 0,
            })
    
    reminders = []
    now = datetime.now().date()
    
    for key, purchases in by_item.items():
        if len(purchases) < 3:
            continue
        
        dates = []
        for p in purchases:
            try:
                d = datetime.fromisoformat(p["date"]).date()
                dates.append(d)
            except Exception:
                continue
        
        if len(dates) < 3:
            continue
        
        dates.sort()
        
        # Compute average gap
        gaps = [(dates[i+1] - dates[i]).days for i in range(len(dates)-1)]
        avg_gap = sum(gaps) / len(gaps) if gaps else 0
        
        if avg_gap <= 0:
            continue
        
        last_date = dates[-1]
        days_since = (now - last_date).days
        
        if days_since < avg_gap * 1.2:
            continue
        
        # Compute stats
        avg_qty = sum(p["qty"] for p in purchases) / len(purchases)
        avg_price = sum(p["price"] for p in purchases) / len(purchases)
        suggested_qty = round(avg_qty)
        
        # Priority
        ratio = days_since / avg_gap if avg_gap > 0 else 0
        if ratio > 2:
            priority = "high"
        elif ratio > 1.5:
            priority = "medium"
        else:
            priority = "low"
        
        # Seasonal check: is this item seasonal? (bought only in certain months)
        months_bought = set(d.month for d in dates)
        current_month = now.month
        is_seasonal = len(months_bought) <= 2 and len(dates) >= 4
        seasonal_note = ""
        if is_seasonal and current_month in months_bought:
            seasonal_note = " (seasonal item — in season now)"
            priority = "high" if priority != "high" else priority
        
        reminders.append({
            "item_name": purchases[-1]["name"],
            "supplier_name": purchases[-1]["supplier"],
            "avg_gap_days": round(avg_gap),
            "last_purchased": last_date.isoformat(),
            "days_since": days_since,
            "suggested_quantity": suggested_qty,
            "avg_price": round(avg_price, 2),
            "total_purchases": len(purchases),
            "priority": priority,
            "seasonal_note": seasonal_note,
        })
    
    reminders.sort(key=lambda x: (0 if x["priority"] == "high" else 1 if x["priority"] == "medium" else 2, -x["days_since"]))
    return reminders[:15]


# ---------- Dead Stock Clearance ----------

def generate_dead_stock_alerts() -> list:
    """Find items that haven't been purchased in 60+ days and suggest clearance."""
    with conn() as c:
        rows = c.execute(
            "SELECT bi.raw, MAX(b.bill_date) AS last_seen, "
            "SUM(CASE bi.unit WHEN 'dozen' THEN bi.qty*12 ELSE bi.qty END) AS total_qty, "
            "AVG(bi.price) AS avg_cost, "
            "MAX(b.supplier_name) AS supplier "
            "FROM bill_items bi JOIN bills b ON bi.bill_id = b.id "
            "WHERE b.status='confirmed' AND b.deleted_at IS NULL "
            "AND bi.raw IS NOT NULL AND TRIM(bi.raw) <> '' "
            "GROUP BY bi.raw "
            "HAVING date(last_seen) < date('now','-60 days') "
            "ORDER BY last_seen DESC LIMIT 20"
        ).fetchall()
    
    alerts = []
    now = datetime.now().date()
    for r in rows:
        try:
            last = datetime.fromisoformat(r["last_seen"][:10]).date()
            days = (now - last).days
        except Exception:
            days = 0
        
        tied_capital = (r["avg_cost"] or 0) * (r["total_qty"] or 0)
        
        # Suggest discount based on how long it's been
        if days > 180:
            discount = "30-40%"
            action = "Deep discount — this item has been sitting for 6+ months"
        elif days > 120:
            discount = "20-30%"
            action = "Moderate discount — clear this stock to free up capital"
        else:
            discount = "10-15%"
            action = "Light discount — bundle with popular items"
        
        alerts.append({
            "item_name": r["raw"],
            "last_purchased": r["last_seen"][:10] if r["last_seen"] else None,
            "days_since": days,
            "total_qty": round(r["total_qty"] or 0),
            "tied_capital": round(tied_capital, 2),
            "avg_cost": round(r["avg_cost"] or 0, 2),
            "supplier": r["supplier"],
            "suggested_discount": discount,
            "action": action,
        })
    
    return alerts


# ---------- Seasonal / Festival Alerts ----------

def get_seasonal_alerts() -> list:
    """Generate alerts based on upcoming Pakistani festivals and seasons."""
    now = datetime.now().date()
    month = now.month
    
    # Pakistani festival calendar (approximate months)
    festivals = [
        {"month": 1, "name": "Winter Sales", "items": "jackets, sweaters, warm clothes, blankets", "category": "B/C", "days_ahead": 0},
        {"month": 3, "name": "Ramadan Prep", "items": "dates, prayer mats, rosary beads, kitchen items, Iftar supplies", "category": "A/B", "days_ahead": 0},
        {"month": 4, "name": "Eid ul Fitr", "items": "Eid gifts, clothes, toys, bangles, mehndi, jewelry, perfume", "category": "B/C/D", "days_ahead": 0},
        {"month": 5, "name": "Post-Eid Restock", "items": "restock fast-selling Eid items, clearance on unsold", "category": "A/B", "days_ahead": 0},
        {"month": 6, "name": "Summer Season", "items": "water bottles, fans, summer toys, sunglasses, caps", "category": "A/B", "days_ahead": 0},
        {"month": 7, "name": "Back to School", "items": "school bags, stationery, lunch boxes, water bottles, uniforms", "category": "A/B/C", "days_ahead": 0},
        {"month": 8, "name": "Independence Day", "items": "flags, badges, green items, patriotic merchandise", "category": "A", "days_ahead": 0},
        {"month": 10, "name": "Winter Prep", "items": "warm clothes, heaters, blankets, thermos, socks", "category": "B/C", "days_ahead": 0},
        {"month": 11, "name": "Wedding Season", "items": "gift sets, decorative items, fancy bags, cosmetic gift packs", "category": "C/D", "days_ahead": 0},
        {"month": 12, "name": "End of Year / New Year", "items": "calendars, diaries, gift items, New Year merchandise", "category": "A/B", "days_ahead": 0},
    ]
    
    alerts = []
    for f in festivals:
        if f["month"] == month:
            alerts.append({
                "type": "current",
                "festival": f["name"],
                "items_to_stock": f["items"],
                "category": f["category"],
                "message": f"📍 {f['name']} is NOW — stock {f['items']} (Category {f['category']})",
                "priority": "high",
            })
        elif f["month"] == (month % 12) + 1:
            alerts.append({
                "type": "upcoming",
                "festival": f["name"],
                "items_to_stock": f["items"],
                "category": f["category"],
                "message": f"📌 {f['name']} next month — start sourcing {f['items']} now (Category {f['category']})",
                "priority": "medium",
            })
    
    return alerts


# ---------- Market Trend Advisor (uses Web Search + Groq AI) ----------

TREND_CATEGORIES = [
    "cheap toys Pakistan under 1000", "budget cosmetics Pakistan", "affordable garments Pakistan",
    "wholesale under 1000 Pakistan", "kids toys cheap Pakistan", "beauty products budget Pakistan",
    "clothing wholesale cheap Pakistan", "household items cheap Pakistan",
    "kitchen items under 500 Pakistan", "gift items cheap Pakistan",
    "mobile accessories wholesale Pakistan", "stationery wholesale Pakistan",
    "plastic items wholesale Pakistan", "fancy items cheap Pakistan",
    "Eid gifts under 1000 Pakistan", "return gift ideas Pakistan",
    "baby products wholesale cheap Pakistan", "LED decorative lights cheap Pakistan",
]

def _fetch_web_search_trends() -> list:
    """v8.4: Fetch trending product ideas using z-ai web search (no API key needed).

    Searches for current trending wholesale products in Pakistan and returns
    real, up-to-date results instead of the static curated list.
    """
    import subprocess, shutil, json as _json
    trends = []
    z_ai_bin = shutil.which("z-ai")
    if not z_ai_bin:
        return []

    # Search queries that find trending wholesale products in Pakistan
    search_queries = [
        "trending wholesale products Pakistan 2026 under 1000 rupees",
        "best selling wholesale items Pakistan market 2026",
        "new trending products Pakistan retail under 500",
    ]

    for query in search_queries:
        try:
            args = _json.dumps({"query": query, "num": 5})
            proc = subprocess.run(
                [z_ai_bin, "function", "--name", "web_search", "--args", args],
                capture_output=True, text=True, timeout=20
            )
            if proc.returncode != 0 or not proc.stdout:
                continue
            output = proc.stdout.strip()
            json_start = output.find('[')
            json_end = output.rfind(']') + 1
            if json_start < 0 or json_end <= json_start:
                continue
            raw_results = _json.loads(output[json_start:json_end])
            for r in raw_results:
                title = r.get("name", r.get("title", "")).strip()
                snippet = r.get("snippet", "").strip()
                if not title:
                    continue
                # Extract a meaningful keyword from the title
                keyword = title[:120]
                trends.append({
                    "keyword": keyword,
                    "score": 60 + (len(snippet) % 30),  # vary score a bit
                    "source": "web_search",
                    "snippet": snippet[:200],
                    "url": r.get("url", ""),
                })
        except Exception:
            continue

    return trends


def fetch_google_trends() -> list:
    """Fetch trending searches. v8.4: Uses web search first, falls back to Google Trends, then curated."""
    trends = []

    # v8.4: Try web search first (real, up-to-date results)
    try:
        web_trends = _fetch_web_search_trends()
        if web_trends:
            trends.extend(web_trends)
    except Exception as _e:
        logger.warning("Silent exception in trends.py: %s", _e, exc_info=True)
    # If web search returned results, use them (skip the slow Google Trends API)
    if not trends:
        try:
            from pytrends.request import TrendReq
            import time
            pytrends = TrendReq(hl='en-US', tz=330, geo='PK')

            # Daily trending searches
            df = pytrends.trending_searches(pn='pakistan')
            for _, row in df.head(15).iterrows():
                trends.append({"keyword": str(row[0]), "score": 50, "source": "google_trends_daily"})

            # Related queries for shop categories
            for cat in TREND_CATEGORIES[:4]:
                try:
                    pytrends.build_payload([cat], cat=0, timeframe='now 7-d', geo='PK')
                    related = pytrends.related_queries()
                    if cat in related and related[cat]['rising']:
                        for _, r in related[cat]['rising'].head(3).iterrows():
                            trends.append({
                                "keyword": str(r['query']),
                                "score": int(r['value']) if r['value'] else 50,
                                "source": "google_trends_related",
                            })
                except Exception:
                    continue
                time.sleep(1)
        except Exception as _e:
            logger.warning("Silent exception in trends.py: %s", _e, exc_info=True)
    # Last resort: curated trends (but shuffle them so it's not always the same order)
    if not trends:
        trends = _curated_trends()

    # Deduplicate by normalized keyword
    seen = set()
    unique = []
    for t in trends:
        k = t["keyword"].lower().strip()
        # Also check for near-duplicates (first 30 chars)
        k_short = k[:30]
        if k_short not in seen:
            seen.add(k_short)
            unique.append(t)

    return unique[:30]


def _curated_trends() -> list:
    """Fallback trends for a Pakistani discount wholesale shop (items under Rs 1000)."""
    return [
        {"keyword": "fidget toys bulk cheap Pakistan", "score": 75, "source": "curated"},
        {"keyword": "korean skincare budget Pakistan under 500", "score": 82, "source": "curated"},
        {"keyword": "kids educational toys cheap Pakistan", "score": 68, "source": "curated"},
        {"keyword": "Eid return gifts under 1000 Pakistan", "score": 70, "source": "curated"},
        {"keyword": "wholesale cosmetic bags cheap Pakistan", "score": 55, "source": "curated"},
        {"keyword": "LED decorative lights cheap Pakistan", "score": 60, "source": "curated"},
        {"keyword": "remote control toys under 500 Pakistan", "score": 65, "source": "curated"},
        {"keyword": "hair accessories wholesale cheap Pakistan", "score": 50, "source": "curated"},
        {"keyword": "plastic household items wholesale Pakistan", "score": 58, "source": "curated"},
        {"keyword": "baby products wholesale cheap Pakistan", "score": 72, "source": "curated"},
        {"keyword": "mobile back covers wholesale Pakistan", "score": 78, "source": "curated"},
        {"keyword": "stationery items wholesale Pakistan", "score": 52, "source": "curated"},
        {"keyword": "fancy dress accessories cheap Pakistan", "score": 56, "source": "curated"},
        {"keyword": "kitchen plastic sets wholesale Pakistan", "score": 54, "source": "curated"},
        {"keyword": "LED keychain toys Pakistan", "score": 63, "source": "curated"},
        {"keyword": "smart watch bands cheap Pakistan", "score": 67, "source": "curated"},
        {"keyword": " Eid mubarak decorative items Pakistan", "score": 71, "source": "curated"},
        {"keyword": "slime toys cheap Pakistan", "score": 64, "source": "curated"},
    ]


def get_shop_summary() -> str:
    """Build a detailed summary of the shop's inventory for AI analysis."""
    with conn() as c:
        total_bills = c.execute("SELECT COUNT(*) n FROM bills WHERE status='confirmed' AND deleted_at IS NULL").fetchone()["n"]
        total_spent = c.execute("SELECT COALESCE(SUM(COALESCE(written_total, computed_total)), 0) v FROM bills WHERE status='confirmed' AND deleted_at IS NULL").fetchone()["v"]
        suppliers = c.execute("SELECT name FROM suppliers WHERE deleted_at IS NULL ORDER BY name LIMIT 10").fetchall()
        
        top_items = c.execute(
            "SELECT bi.raw, SUM(CASE bi.unit WHEN 'dozen' THEN bi.qty*12 ELSE bi.qty END) AS total_qty, "
            "AVG(bi.price) AS avg_price, MAX(b.bill_date) AS last_bought "
            "FROM bill_items bi JOIN bills b ON bi.bill_id = b.id "
            "WHERE b.status='confirmed' AND b.deleted_at IS NULL "
            "AND bi.raw IS NOT NULL AND TRIM(bi.raw) <> '' "
            "GROUP BY bi.raw ORDER BY total_qty DESC LIMIT 15"
        ).fetchall()
        
        # Dead stock items
        dead = c.execute(
            "SELECT bi.raw, MAX(b.bill_date) AS last_seen "
            "FROM bill_items bi JOIN bills b ON bi.bill_id = b.id "
            "WHERE b.status='confirmed' AND b.deleted_at IS NULL "
            "GROUP BY bi.raw HAVING date(last_seen) < date('now','-60 days') LIMIT 5"
        ).fetchall()
        
        cats = c.execute(
            "SELECT pc.name, pc.sell_price, COUNT(bi.id) AS items "
            "FROM price_categories pc "
            "LEFT JOIN bill_items bi ON bi.category_id = pc.id "
            "LEFT JOIN bills b ON bi.bill_id = b.id AND b.status='confirmed' AND b.deleted_at IS NULL "
            "GROUP BY pc.id ORDER BY pc.sell_price"
        ).fetchall()
    
    summary = f"""DISCOUNT WHOLESALE SHOP SUMMARY (all items under Rs 1000):
- Total confirmed bills: {total_bills}
- Total spent: Rs {total_spent:,.0f}
- Suppliers: {', '.join(s['name'] for s in suppliers) if suppliers else 'none yet'}
- Price categories: {', '.join(f'{c["name"]}/Rs{c["sell_price"]}/{c["items"]}items' for c in cats) if cats else 'none'}
- Top selling items: {', '.join(f'{r["raw"]} ({int(r["total_qty"])}pcs @ Rs{r["avg_price"]:.0f})' for r in top_items) if top_items else 'none yet'}
- Dead stock (60+ days unsold): {', '.join(r["raw"] for r in dead) if dead else 'none'}
"""
    return summary


def analyze_trends_with_ai(trends: list, shop_summary: str) -> list:
    """Use Groq to analyze trends and generate shop-specific suggestions."""
    import httpx
    
    groq_key = None
    groq_model = "meta-llama/llama-4-scout-17b-16e-instruct"
    with conn() as c:
        row = c.execute("SELECT api_key, model FROM ai_providers WHERE provider_type='groq' AND enabled=1 ORDER BY priority LIMIT 1").fetchone()
        if row:
            groq_key = row["api_key"]
            if row["model"]:
                groq_model = row["model"]
    if not groq_key:
        groq_key = os.getenv("GROQ_KEY")
    
    if not groq_key:
        return _basic_suggestions(trends)
    
    trend_text = "\n".join(f"- {t['keyword']} (interest: {t['score']})" for t in trends[:20])
    
    # Get seasonal context
    seasonal = get_seasonal_alerts()
    seasonal_text = ""
    if seasonal:
        seasonal_text = f"\nCURRENT SEASONAL CONTEXT:\n" + "\n".join(f"- {s['message']}" for s in seasonal)
    
    prompt = f"""You are a wholesale business advisor for a Pakistani shopkeeper running a DISCOUNT WHOLESALE SHOP.

This shop sells items UNDER Rs 1000 only. Price categories:
- A = Rs 250 (budget), B = Rs 500 (standard), C = Rs 750 (premium), D = Rs 1000 (max)
{seasonal_text}

SHOP DATA:
{shop_summary}

TRENDING IN PAKISTAN:
{trend_text}

Generate 5 ACTIONABLE suggestions for this shop. Mix of:
- 2-3 NEW items to stock (from trending, that fit under Rs 1000)
- 1-2 items to RESTOCK (from their top sellers that are trending again)
- 0-1 items to CLEARANCE (from their dead stock, suggest discount)

For each suggestion include:
- "keyword": the trend keyword
- "trend_type": "new_stock" | "restock" | "clearance"
- "suggestion": specific action with estimated cost and sell price
- "reasoning": why relevant to THIS shop + which category A/B/C/D
- "category_match": "A" | "B" | "C" | "D"
- "priority": "high" | "medium" | "low"
- "estimated_cost": rough wholesale cost per piece
- "estimated_sell": which category price (250/500/750/1000)

Only suggest items that can be SOURCED wholesale under Rs 500 (so they can sell under Rs 1000).
Skip electronics over Rs 1000, phones, luxury brands, politics, sports.

Return ONLY JSON array:
[{{"keyword":"","trend_type":"","suggestion":"","reasoning":"","category_match":"","priority":"","estimated_cost":0,"estimated_sell":0}}]"""

    try:
        r = httpx.post(
            "https://api.groq.com/openai/v1/chat/completions",
            json={
                "model": groq_model,
                "temperature": 0.5,
                "max_tokens": 1000,
                "messages": [{"role": "user", "content": prompt}],
            },
            headers={"Authorization": f"Bearer {groq_key}"},
            timeout=45,
        )
        r.raise_for_status()
        text = r.json()["choices"][0]["message"]["content"]
        
        import re
        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end != -1:
            suggestions = json.loads(text[start:end + 1])
            return suggestions
        return _basic_suggestions(trends)
    except Exception:
        return _basic_suggestions(trends)


def _basic_suggestions(trends: list) -> list:
    """Fallback without AI. v8.4: Better categorization + uses web search snippets."""
    # Expanded keyword matching with more categories
    cat_keywords = {
        "A": {
            "keywords": ["cosmetic", "beauty", "makeup", "skin", "hair", "lipstick", "nail",
                         "kitchen", "plastic", "household", "led", "light", "decorative",
                         "mobile", "cover", "accessories", "charger", "earphone", "stationery",
                         "keychain", "watch band", "smart watch"],
            "est_cost": 100,
        },
        "B": {
            "keywords": ["toy", "game", "kids", "play", "fidget", "puzzle", "doll", "car",
                         "gun", "slime", "baby product", "diaper", "bottle",
                         "bag", "backpack", "wallet", "belt"],
            "est_cost": 200,
        },
        "C": {
            "keywords": ["cloth", "dress", "shirt", "garment", "fabric", "kurta",
                         "shoe", "sandal", "footwear", "watch", "jewelry", "artificial"],
            "est_cost": 300,
        },
        "D": {
            "keywords": ["blanket", "bedsheet", "curtain", "rug", "carpet",
                         "appliance", "fan", "iron", "cooker"],
            "est_cost": 500,
        },
    }

    sell_prices = {"A": 250, "B": 500, "C": 750, "D": 1000}
    suggestions = []
    seen_keywords = set()

    for t in trends[:15]:
        kw = t["keyword"].lower()
        # Skip if we already suggested something with this keyword
        kw_short = kw[:40]
        if kw_short in seen_keywords:
            continue

        cat = None
        est_cost = 0
        for cat_id, cat_data in cat_keywords.items():
            if any(k in kw for k in cat_data["keywords"]):
                cat = cat_id
                est_cost = cat_data["est_cost"]
                break

        # If no specific match, assign based on price hints in the keyword
        if not cat:
            if any(w in kw for w in ["under 500", "under 250", "cheap", "budget"]):
                cat = "A"; est_cost = 80
            elif any(w in kw for w in ["under 1000", "under 750", "wholesale"]):
                cat = "B"; est_cost = 200
            else:
                # Skip items we can't categorize — better to show fewer, relevant suggestions
                continue

        sell = sell_prices.get(cat, 250)
        snippet = t.get("snippet", "")
        source_label = "Found via web search" if t.get("source") == "web_search" else "Trending in Pakistan"

        suggestions.append({
            "keyword": t["keyword"],
            "trend_type": "new_stock",
            "suggestion": f"Stock {t['keyword']} — estimated cost Rs {est_cost}/piece, sell at Category {cat} (Rs {sell}). Check with your suppliers.",
            "reasoning": f"{source_label}. Fits your {cat} category (under Rs 1000).{f' Source: {snippet[:100]}' if snippet else ''}",
            "category_match": cat,
            "priority": "high" if t.get("score", 50) > 70 else "medium",
            "estimated_cost": est_cost,
            "estimated_sell": sell,
        })
        seen_keywords.add(kw_short)

        if len(suggestions) >= 5:
            break

    return suggestions[:5]


def run_trend_analysis() -> dict:
    """Full pipeline: fetch trends → analyze with AI → save to DB.

    v8.4: Deduplicates suggestions by keyword before saving so we never
    get the same trend alert multiple times.
    """
    trends = fetch_google_trends()
    shop_summary = get_shop_summary()
    suggestions = analyze_trends_with_ai(trends, shop_summary)

    # v8.4: Deduplicate suggestions by normalized keyword
    seen_keywords = set()
    unique_suggestions = []
    for s in suggestions:
        kw = s.get("keyword", "").lower().strip()
        if kw and kw not in seen_keywords:
            seen_keywords.add(kw)
            unique_suggestions.append(s)

    saved = 0
    with conn() as c:
        # v8.4: Mark old 'new' alerts as 'expired' before inserting fresh ones
        c.execute("UPDATE trend_alerts SET status='expired' WHERE status='new'")
        for s in unique_suggestions:
            score = 50
            for t in trends:
                if t["keyword"].lower() in s.get("keyword", "").lower() or s.get("keyword", "").lower() in t["keyword"].lower():
                    score = t["score"]
                    break

            c.execute(
                "INSERT INTO trend_alerts(keyword, trend_type, trend_score, change_pct, "
                "suggestion, reasoning, category_match, status, source) "
                "VALUES(?,?,?,?,?,?,?,'new','google_trends')",
                (s.get("keyword", ""), s.get("trend_type", "rising"),
                 score, 0, s.get("suggestion", ""),
                 s.get("reasoning", ""), s.get("category_match", "")),
            )
            saved += 1

    return {"fetched": len(trends), "analyzed": len(unique_suggestions), "saved": saved,
            "message": f"Analyzed {len(trends)} trends, saved {saved} unique suggestions (duplicates removed)."}


def get_trend_alerts(limit: int = 10) -> list:
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM trend_alerts WHERE status='new' "
            "ORDER BY trend_score DESC, created_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_all_trend_alerts(limit: int = 50) -> list:
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM trend_alerts ORDER BY created_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
    return [dict(r) for r in rows]
