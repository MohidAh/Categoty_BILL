"""v8.16.0 — AI Market Intelligence Agent.

Searches the web for trending wholesale products, seasonal items, and market
opportunities in Pakistan, then feeds the results + the shop's price categories
to the LLM (Gemini/Groq) for structured recommendations.

Architecture: Search → Retrieve → Analyze → Synthesize (Perplexity-style)

The agent:
1. Reads the shop's price categories from the DB (A=Rs 250, B=Rs 500, etc.)
2. Runs 3-4 web searches for trending products, seasonal items, wholesale prices
3. Feeds all search results + shop context to the LLM
4. Returns structured recommendations with source URLs
"""
import json
import logging
from datetime import datetime
from .db import conn, get_setting

logger = logging.getLogger(__name__)


def _get_shop_context() -> dict:
    """Read the shop's business context for the AI prompt."""
    with conn() as c:
        cats = c.execute(
            "SELECT code, name, sell_price FROM price_categories "
            "WHERE active=1 ORDER BY sell_price"
        ).fetchall()
        shop_name = get_setting("shop_name", "BillBook Shop")
        business_type = get_setting("business_type", "wholesale")
    
    categories = []
    for cat in cats:
        categories.append({
            "code": cat["code"] or "?",
            "name": cat["name"] or "",
            "sell_price": float(cat["sell_price"] or 0),
        })
    
    current_month = datetime.now().strftime("%B %Y")
    
    # Determine upcoming season/event
    month = datetime.now().month
    season = ""
    if month in [11, 12, 1, 2]:
        season = "Winter season — blankets, heaters, warm clothing, dry fruits, winter accessories are in high demand"
    elif month in [3, 4]:
        season = "Spring — clothing, home decor, garden items are trending"
    elif month == 5 or month == 6:
        season = "Pre-monsoon — umbrellas, rain gear, summer cooling products, cold drinks"
    elif month in [7, 8]:
        season = "Monsoon season — umbrellas, rain coats, waterproof items"
    elif month == 9:
        season = "Back to school — stationery, bags, lunch boxes, school supplies"
    elif month in [10, 11]:
        season = "Pre-winter + Eid season (if applicable) — warm clothing, gifts, kitchenware, home appliances"
    
    return {
        "shop_name": shop_name,
        "business_type": business_type,
        "price_categories": categories,
        "current_month": current_month,
        "seasonal_context": season,
        "location": "Pakistan",
    }


def _run_web_searches() -> list:
    """Run multiple web searches for market intelligence data."""
    import subprocess, shutil
    
    queries = [
        f"trending wholesale products to sell in Pakistan {datetime.now().year} market demand",
        f"Pakistan wholesale market trends {datetime.now().strftime('%B')} what products are selling",
        "wholesale product price list Pakistan bazaar market 2025 2026 trending items",
    ]
    
    all_results = []
    z_ai_bin = shutil.which("z-ai")
    if not z_ai_bin:
        logger.warning("z-ai CLI not found — skipping web search")
        return all_results
    
    for query in queries:
        try:
            args = json.dumps({"query": query, "num": 5})
            proc = subprocess.run(
                [z_ai_bin, "function", "--name", "web_search", "--args", args],
                capture_output=True, text=True, timeout=30
            )
            if proc.returncode == 0 and proc.stdout:
                # Find the JSON array in the output
                output = proc.stdout.strip()
                json_start = output.find('[')
                json_end = output.rfind(']') + 1
                if json_start >= 0 and json_end > json_start:
                    results = json.loads(output[json_start:json_end])
                    for r in results[:3]:  # top 3 per search
                        all_results.append({
                            "title": r.get("name", r.get("title", ""))[:200],
                            "url": r.get("url", ""),
                            "snippet": r.get("snippet", "")[:300],
                        })
        except Exception as e:
            logger.warning("Web search failed for query '%s': %s", query, e)
    
    return all_results


def _build_market_intel_prompt(shop_context: dict, search_results: list) -> str:
    """Build the LLM prompt for market intelligence analysis."""
    categories_str = "\n".join([
        f"  - Category {c['code']}: sell price Rs {c['sell_price']:.0f} ({c['name']})"
        for c in shop_context["price_categories"]
    ])
    
    search_context = ""
    if search_results:
        search_context = "\n\nWeb search results (real-time market data):\n"
        for i, r in enumerate(search_results, 1):
            search_context += f"\n{i}. {r['title']}\n   {r['snippet']}\n   URL: {r['url']}\n"
    else:
        search_context = "\n\n(No web search results available — use your training data about Pakistani wholesale markets.)\n"
    
    return f"""You are a wholesale business advisor for a Pakistani discount shop.

SHOP CONTEXT:
- Shop: {shop_context['shop_name']}
- Type: {shop_context['business_type']} wholesale discount shop
- Location: Pakistan
- Current month: {shop_context['current_month']}
- Seasonal context: {shop_context['seasonal_context']}

PRICE CATEGORIES (the shop sells by these fixed price tiers, not by individual items):
{categories_str}

TASK:
Based on the web search results and your knowledge of Pakistani wholesale markets, suggest 5-7 products the shop owner should consider stocking.

For EACH product, provide:
1. "product_name": Short name of the product
2. "estimated_wholesale_cost": Estimated wholesale cost in PKR (what they'd pay to buy it)
3. "suggested_category": Which price category it fits (use the code, e.g. "A", "B", "C", "D")
4. "estimated_margin_pct": Estimated margin percentage if sold at the category price
5. "why": 1-2 sentence explanation of why this is a good product to stock right now
6. "source_url": URL from the search results that supports this recommendation (or empty string)

GUIDELINES:
- Focus on products that make sense for a Pakistani wholesale discount shop
- Consider the seasonal context ({shop_context['seasonal_context']})
- The wholesale cost should be LESS than the category sell price (positive margin)
- Prefer products that are trending or have high demand
- Include at least 2 products from the web search results
- Be realistic about wholesale costs in Pakistan

Return ONLY a JSON array:
[{{"product_name": "...", "estimated_wholesale_cost": 0, "suggested_category": "A", "estimated_margin_pct": 0, "why": "...", "source_url": ""}}]
"""


def generate_market_intelligence() -> dict:
    """Run the full market intelligence pipeline.
    
    Returns:
        {
            "recommendations": [...],
            "search_results": [...],
            "shop_context": {...},
            "generated_at": "2026-08-22 ..."
        }
    """
    from . import ai_router
    
    # Step 1: Read shop context
    shop_context = _get_shop_context()
    
    # Step 2: Run web searches
    search_results = _run_web_searches()
    
    # Step 3: Build prompt
    prompt = _build_market_intel_prompt(shop_context, search_results)
    
    # Step 4: Call the LLM
    groq_key = get_setting("groq_api_key", "")
    groq_model = get_setting("groq_model", "llama-3.3-70b-versatile")
    gemini_key = get_setting("gemini_api_key", "")
    
    # Decrypt keys if needed
    try:
        from .crypto import decrypt_setting_key
        if gemini_key:
            gemini_key = decrypt_setting_key("gemini_api_key", gemini_key)
        if groq_key:
            groq_key = decrypt_setting_key("groq_api_key", groq_key)
    except Exception:
        pass
    
    recommendations = []
    
    # Try Gemini first (better at structured output)
    if gemini_key:
        try:
            recommendations = _call_gemini_for_market_intel(gemini_key, prompt)
        except Exception as e:
            logger.warning("Gemini market intel failed: %s", e)
    
    # Fallback to Groq
    if not recommendations and groq_key:
        try:
            recommendations = _call_groq_for_market_intel(groq_key, groq_model, prompt)
        except Exception as e:
            logger.warning("Groq market intel failed: %s", e)
    
    # If all AI providers failed, return search results without AI analysis
    return {
        "recommendations": recommendations,
        "search_results": search_results,
        "shop_context": {
            "categories": shop_context["price_categories"],
            "seasonal_context": shop_context["seasonal_context"],
            "current_month": shop_context["current_month"],
        },
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "ai_provider": "gemini" if gemini_key else ("groq" if groq_key else "none"),
    }


def _call_gemini_for_market_intel(api_key: str, prompt: str) -> list:
    """Call Gemini API for market intelligence analysis."""
    import httpx
    
    model = "gemini-2.5-flash"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    
    r = httpx.post(
        url,
        headers={"Content-Type": "application/json"},
        params={"key": api_key},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.3, "maxOutputTokens": 2048},
        },
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    
    text = data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
    
    # Parse JSON from the response
    return _parse_recommendations(text)


def _call_groq_for_market_intel(api_key: str, model: str, prompt: str) -> list:
    """Call Groq API for market intelligence analysis."""
    import httpx
    
    url = "https://api.groq.com/openai/v1/chat/completions"
    
    r = httpx.post(
        url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens": 2048,
        },
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    
    text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    
    return _parse_recommendations(text)


def _parse_recommendations(text: str) -> list:
    """Parse JSON recommendations from LLM response text."""
    import re
    
    # Strip markdown fences
    text = text.strip()
    if "```" in text:
        match = re.search(r'```(?:json)?\s*\n?(.*?)```', text, re.DOTALL)
        if match:
            text = match.group(1).strip()
    
    # Find JSON array
    start = text.find('[')
    end = text.rfind(']') + 1
    if start >= 0 and end > start:
        try:
            recs = json.loads(text[start:end])
            # Validate each recommendation
            valid = []
            for rec in recs:
                if not isinstance(rec, dict):
                    continue
                valid.append({
                    "product_name": rec.get("product_name", "Unknown"),
                    "estimated_wholesale_cost": float(rec.get("estimated_wholesale_cost", 0) or 0),
                    "suggested_category": rec.get("suggested_category", "?"),
                    "estimated_margin_pct": float(rec.get("estimated_margin_pct", 0) or 0),
                    "why": rec.get("why", ""),
                    "source_url": rec.get("source_url", ""),
                })
            return valid
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            logger.warning("Failed to parse market intel JSON: %s", e)
    
    return []
