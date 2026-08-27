"""AI-based bill extraction with multi-provider fallback."""
import base64
import json
import logging
import os
from pathlib import Path
import httpx
from dotenv import load_dotenv
from .db import conn, get_setting

load_dotenv()

logger = logging.getLogger(__name__)


def _enforce_ai_guardrails(provider: str):
    """v8.5: pre-flight check before every AI provider call.

    1. Kill switch — if `automation_config.ai_kill_switch` is enabled,
       raise RuntimeError("AI is disabled").
    2. Daily budget — if the count of non-cached ai_usage rows for this
       provider today is >= the configured limit (default groq=500,
       gemini=100), raise RuntimeError("Daily AI budget exhausted").

    This is called by call_gemini, call_openai_style, and test_gemini
    so budget enforcement cannot be bypassed by callers that bypass
    ai_router.ai_call (which is most of extract.py).
    """
    # 1. Kill switch
    with conn() as c:
        row = c.execute(
            "SELECT enabled FROM automation_config WHERE key='ai_kill_switch'"
        ).fetchone()
    if row and int(row["enabled"] or 0):
        raise RuntimeError("AI is disabled (kill switch active)")
    # 2. Daily budget
    key = f"max_ai_calls_per_day_{provider}"
    default_limit = "500" if provider == "groq" else "100"
    try:
        limit = int(get_setting(key, default_limit) or default_limit)
    except (TypeError, ValueError):
        limit = int(default_limit)
    if limit <= 0:
        return  # budget tracking disabled
    with conn() as c:
        row = c.execute(
            "SELECT COUNT(*) AS n FROM ai_usage "
            "WHERE provider=? AND cached=0 AND date(created_at)=date('now','localtime')",
            (provider,),
        ).fetchone()
    if row and int(row["n"] or 0) >= limit:
        raise RuntimeError(
            f"Daily AI budget exhausted for {provider} "
            f"({row['n']}/{limit} calls today)"
        )


PROMPT = """You are an expert at reading Pakistani wholesale bills. These bills may be:
- Handwritten in Urdu (Urdu numerals or Arabic numerals)
- Handwritten in English
- Printed invoices with Urdu or English text
- Mix of Urdu and English
- Written in red pen, blue pen, or pencil
- Photographs of physical bills or scanned PDFs

For each line item on the bill, extract:
- "price": the actual COST PRICE per piece (unit cost). If the bill shows a line total instead of unit price, compute: unit_price = line_total ÷ total_quantity.
- "qty_as_written": the quantity as written on the bill
- "sell_price": the sell price category. Recognize these KEYWORDS written near the item:
    "A" or "250" → 250
    "B" or "500" → 500
    "C" or "750" → 750
    "D" or "1000" → 1000
  If you see one of these keywords, put the number here. Otherwise null.
- "raw": transcribe the line exactly as you see it (include Urdu text if present)
- "marks": note anything unusual (tally marks, dozen, smudged, crossed_out, split, etc.)
- "confidence": 0.0 to 1.0
- "needs_review": true if you're unsure

RULES:
1. Find the GRAND TOTAL on the bill (usually at bottom, often with a dash like "47190-" or Urdu فقط)
2. "price" must always be the UNIT COST per piece, never the line total
3. A number starting with 03 or +92 is a PHONE number
4. If a number is crossed out, skip that line
5. Read Urdu numerals correctly: ۰=0, ۱=1, ۲=2, ۳=3, ۴=4, ۵=5, ۶=6, ۷=7, ۸=8, ۹=9
6. Set needs_review=true if you're unsure about any field
7. QUANTITY: Use the total quantity in pieces. Some bills have a "QTY" column (carton count) and a "T.QTY" or "TOTAL QTY" column (total pieces). If both are present, use T.QTY. If only carton count is shown, multiply by pieces-per-carton (e.g. 2 CTN × 6 DOZ = 144 pieces). If neither is present, use the quantity as written. Common conversions: 1 DOZ = 12, 6 DOZ = 72, 1 SET = pieces in set, 1 PACK = pieces in pack.
8. CATEGORY KEYWORDS: Letters A/B/C/D written next to items on the bill image indicate sell-price categories. A=250, B=500, C=750, D=1000. These may be handwritten in any color.
9. AUTO-SPLIT: If you see MULTIPLE category letters next to a SINGLE product (e.g. "A B C" or "250 500 750" written next to a bottle set), CREATE MULTIPLE LINE ITEMS — one per category. Each line item should have:
   - The same "raw" description (append the category letter for clarity, e.g. "Bottle Set (A)", "Bottle Set (B)", "Bottle Set (C)")
   - The same "qty" (total pieces)
   - A "sell_price" set to the respective category number (250, 500, or 750)
   - A "price" (unit cost) distributed proportionally by sell price.
     Example: bottle set costs Rs 1250 total, split into A(250) + B(500) + C(750):
       Total sell price = 250 + 500 + 750 = 1500
       A gets 250/1500 × 1250 = Rs 208.33
       B gets 500/1500 × 1250 = Rs 416.67
       C gets 750/1500 × 1250 = Rs 625.00
   This way each category row has its own correct unit cost.

Return ONLY JSON:
{"phone": str|null, "supplier_guess": str|null, "bill_date": str|null,
 "written_total": num|null,
 "lines": [{"raw": str, "price": num, "qty_as_written": str, "sell_price": num|null, "marks": str, "confidence": num, "needs_review": bool}],
 "line_confidence": [num 0..1 per line]}"""


def _build_prompt() -> str:
    """v8.13.0: Build the extraction prompt with the shop's ACTUAL category
    list (not just the default A/B/C/D=250/500/750/1000). This lets the AI:

    1. Recognize custom category codes/keywords beyond A/B/C/D
       (e.g. if the shop has "E=Bag Rs 30" or "BAG10=Bag Rs 10").
    2. Auto-suggest a category for each line item when no keyword is written
       on the bill, based on cost-price thresholds derived from the shop's
       actual sell prices. The suggested category is returned as
       `suggested_sell_price` (separate from `sell_price`, which is only set
       when a keyword is actually detected on the bill).

    Falls back to the static PROMPT if the DB is unavailable.
    """
    base = PROMPT
    try:
        from .db import conn
        with conn() as c:
            cats = c.execute(
                "SELECT code, name, sell_price FROM price_categories "
                "WHERE active = 1 ORDER BY sort_order, sell_price"
            ).fetchall()
        if not cats:
            return base
        # Build the dynamic category list
        cat_lines = []
        for cat in cats:
            code = cat["code"] or ""
            name = cat["name"] or ""
            sp = float(cat["sell_price"] or 0)
            cat_lines.append(f'  - Code "{code}" or keyword "{code}" or "{sp:.0f}" → sell_price {sp:.0f} (name: {name})')
        cat_block = "\n".join(cat_lines)
        # Build cost-threshold hints for auto-suggest
        sorted_cats = sorted([{"code": c["code"], "sp": float(c["sell_price"] or 0)} for c in cats],
                             key=lambda x: x["sp"])
        threshold_hints = []
        for i, cat in enumerate(sorted_cats):
            sp = cat["sp"]
            # Suggest this category if cost is between (prev_sp × 0.4) and (sp × 0.5)
            # (rough heuristic: cost is typically 40-60% of sell price in wholesale)
            lower = sorted_cats[i-1]["sp"] * 0.5 if i > 0 else 0
            upper = sp * 0.5
            threshold_hints.append(
                f'  - cost Rs {lower:.0f}–{upper:.0f} → suggest code "{cat["code"]}" (sell Rs {sp:.0f})'
            )
        threshold_block = "\n".join(threshold_hints)

        dynamic_addendum = f"""

DYNAMIC CATEGORY LIST (this shop's actual categories):
{cat_block}

AUTO-SUGGEST (when no keyword is detected on the bill):
If a line item has no visible category keyword, infer the most likely category
from the cost price using these thresholds (rough heuristic — cost is typically
40-50% of sell price in wholesale):
{threshold_block}

For each line, if you inferred a category, set "suggested_sell_price" to that
category's sell price (separate from "sell_price", which stays null when no
keyword is detected). Also set "suggestion_confidence" to 0.0–1.0 (low if the
cost is near a threshold boundary).

The bill review page will pre-fill the category dropdown with your suggestion
but the human can override it.
"""
        # Add suggested_sell_price + suggestion_confidence to the JSON schema
        base = base.replace(
            '"sell_price": num|null, "marks": str, "confidence": num, "needs_review": bool',
            '"sell_price": num|null, "suggested_sell_price": num|null, "suggestion_confidence": num, "marks": str, "confidence": num, "needs_review": bool'
        )
        return base + dynamic_addendum
    except Exception:
        return base


def _b64(p: Path) -> str:
    return base64.b64encode(p.read_bytes()).decode()


def parse_json_loose(text: str) -> dict:
    """Robustly extract a JSON object from a model response.
    
    Handles: markdown fences, text before/after JSON, partial JSON,
    thinking/reasoning blocks, and various model quirks.
    """
    import re as _re
    t = text.strip()
    if not t:
        raise ValueError("Empty response from AI")
    
    # Strip markdown code fences (```json ... ``` or ``` ... ```)
    if "```" in t:
        fence_match = _re.search(r'```(?:json)?\s*\n?(.*?)```', t, _re.DOTALL)
        if fence_match:
            t = fence_match.group(1).strip()
    
    # Remove <think>...</think> blocks (some models add reasoning)
    t = _re.sub(r'<think>.*?</think>', '', t, flags=_re.DOTALL)
    
    # Remove any text before the first { and after the last }
    start = t.find("{")
    end = t.rfind("}")
    if start == -1 or end == -1 or end < start:
        # Maybe the response uses [ ] for an array instead
        start = t.find("[")
        end = t.rfind("]")
        if start != -1 and end != -1 and end > start:
            try:
                arr = json.loads(t[start:end + 1])
                return {"lines": arr, "phone": None, "supplier_guess": None,
                        "bill_date": None, "written_total": None, "line_confidence": []}
            except json.JSONDecodeError:
                pass
        preview = t[:300].replace('\n', ' ')
        raise ValueError(f"No JSON found in response: '{preview}...'")
    
    # Try to parse the extracted JSON
    json_str = t[start:end + 1]
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        pass
    
    # Fix common issues and retry:
    # 1. Trailing commas
    fixed = _re.sub(r',\s*}', '}', json_str)
    fixed = _re.sub(r',\s*]', ']', fixed)
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass
    
    # 2. Single quotes instead of double quotes
    fixed = json_str.replace("'", '"')
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass
    
    # 3. Unquoted keys
    fixed = _re.sub(r'(\w+):', r'"\1":', json_str)
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass
    
    # 4. Try progressively smaller substrings (in case there's garbage after the real JSON)
    for trim_end in range(end, start, -1):
        if t[trim_end] == '}':
            try:
                return json.loads(t[start:trim_end + 1])
            except json.JSONDecodeError:
                continue
    
    preview = t[:300].replace('\n', ' ')
    raise ValueError(f"No JSON in response (model may not support images — "
                     f"use llama-4-scout or gemini for vision): '{preview}...'")


def _img_to_b64(p: Path, max_size: int = 1600, quality: int = 90) -> tuple[str, str]:
    """Convert an image to base64 JPEG with enhanced preprocessing for AI OCR.

    Higher resolution (1600px) + higher quality (90) + sharpening + contrast boost
    helps the AI read handwritten digits more accurately.
    """
    from PIL import Image, ImageEnhance, ImageFilter
    import io

    img = Image.open(p)
    if img.mode != "RGB":
        img = img.convert("RGB")
    # Upscale small images for better OCR
    if max(img.size) < max_size:
        ratio = max_size / max(img.size)
        img = img.resize((int(img.width * ratio), int(img.height * ratio)), Image.LANCZOS)
    elif max(img.size) > max_size * 1.2:
        # Downscale very large images to max_size
        ratio = max_size / max(img.size)
        img = img.resize((int(img.width * ratio), int(img.height * ratio)), Image.LANCZOS)
    # Sharpen to make handwritten digits crisper
    img = img.filter(ImageFilter.UnsharpMask(radius=1, percent=150, threshold=2))
    # Boost contrast to separate ink from paper
    img = ImageEnhance.Contrast(img).enhance(1.25)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    return base64.b64encode(buf.getvalue()).decode(), "image/jpeg"


def call_gemini(pages, key, model="gemini-2.5-flash"):
    # v8.5: enforce AI kill switch + daily budget before every provider call.
    _enforce_ai_guardrails("gemini")
    # Fall back to default if model is None or empty string
    if not model or not model.strip():
        model = "gemini-2.5-flash"
    # Convert images to compressed JPEG (much smaller than PNG)
    parts = [{"text": _build_prompt()}]
    for p in pages:
        data, mime = _img_to_b64(p)
        parts.append({"inline_data": {"mime_type": mime, "data": data}})
    try:
        r = httpx.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            headers={"x-goog-api-key": key},
            json={
                "contents": [{"parts": parts}],
                "generationConfig": {
                    "responseMimeType": "application/json",
                    "temperature": 0,
                },
            },
            timeout=300,  # 5 minutes — allows for multi-page bills
        )
        r.raise_for_status()
    except httpx.HTTPStatusError as e:
        # Enhance 404 error with model-name hint
        if e.response.status_code == 404:
            raise RuntimeError(
                f"Gemini model '{model}' not found (404). "
                f"Check the model name in Settings → AI Providers. "
                f"Valid options: gemini-2.5-flash, gemini-2.0-flash, gemini-1.5-flash, gemini-1.5-pro. "
                f"Original: {e}"
            ) from e
        # Enhance 400/403 with response body
        try:
            body = e.response.json()
            err_msg = body.get("error", {}).get("message", str(body))
        except Exception:
            err_msg = e.response.text[:200]
        raise RuntimeError(f"Gemini API error {e.response.status_code}: {err_msg}") from e
    except httpx.TimeoutException as e:
        raise RuntimeError(
            f"Gemini request timed out (300s). The bill has too many pages or images are too large. "
            f"Try uploading fewer pages, or use a faster model (gemini-2.0-flash). Original: {e}"
        ) from e
    except httpx.RequestError as e:
        raise RuntimeError(f"Gemini network error: {e}") from e

    return parse_json_loose(
        r.json()["candidates"][0]["content"]["parts"][0]["text"]
    )


def call_openai_style(url, model, key, pages):
    # v8.5: enforce AI kill switch + daily budget before every provider call.
    # Infer provider name from URL for budget tracking.
    if "groq.com" in url:
        _enforce_ai_guardrails("groq")
    elif "openrouter.ai" in url:
        _enforce_ai_guardrails("openrouter")
    else:
        _enforce_ai_guardrails("openai_style")
    if not model or not model.strip():
        raise RuntimeError("No model specified for OpenAI-style provider")
    # Convert images to compressed JPEG
    content = [{"type": "text", "text": _build_prompt()}]
    for p in pages:
        data, _ = _img_to_b64(p)
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{data}"}})
    try:
        r = httpx.post(
            url,
            json={
                "model": model,
                "temperature": 0,
                "messages": [{"role": "user", "content": content}],
            },
            headers={"Authorization": f"Bearer {key}"},
            timeout=300,  # 5 minutes
        )
        r.raise_for_status()
    except httpx.HTTPStatusError as e:
        try:
            body = e.response.json()
            err_msg = body.get("error", {}).get("message", str(body))
        except Exception:
            err_msg = e.response.text[:200]
        raise RuntimeError(f"API error {e.response.status_code}: {err_msg}") from e
    except httpx.TimeoutException as e:
        raise RuntimeError(
            f"Request timed out (300s). Try uploading fewer pages. Original: {e}"
        ) from e
    except httpx.RequestError as e:
        raise RuntimeError(f"Network error: {e}") from e
    return parse_json_loose(r.json()["choices"][0]["message"]["content"])


def _providers():
    """Return list of (priority, name, callable) providers.

    DB-configured providers first (sorted by priority), then env-based fallbacks
    (assigned priority 100 so they're tried last).
    """
    providers = []
    try:
        with conn() as c:
            rows = c.execute(
                "SELECT * FROM ai_providers WHERE enabled=1 ORDER BY priority ASC, id ASC"
            ).fetchall()
        for row in rows:
            ptype = row["provider_type"]
            # Decrypt API key if encrypted (Phase 2 crypto)
            from . import crypto as _crypto
            key = _crypto.decrypt_api_key(row["api_key"])
            model = row["model"]
            priority = row["priority"] if row["priority"] is not None else 0
            name = f"{row['name']} ({ptype})"
            if ptype == "gemini":
                m = model if model and model.strip() else "gemini-2.5-flash"
                providers.append((priority, name, lambda p, k=key, m=m: call_gemini(p, k, m)))
            elif ptype == "groq":
                m = model or "meta-llama/llama-4-scout-17b-16e-instruct"
                providers.append((priority, name, lambda p, k=key, m=m: call_openai_style(
                    "https://api.groq.com/openai/v1/chat/completions", m, k, p)))
            elif ptype == "openrouter":
                m = model or "qwen/qwen2.5-vl-32b-instruct:free"
                providers.append((priority, name, lambda p, k=key, m=m: call_openai_style(
                    "https://openrouter.ai/api/v1/chat/completions", m, k, p)))
    except Exception as _e:
        logger.warning("Silent exception in extract.py: %s", _e, exc_info=True)
    # Env-based fallbacks — priority 100 (tried after all DB providers)
    ENV_PRIORITY = 100
    if os.getenv("GEMINI_API_KEY"):
        providers.append((ENV_PRIORITY, "Gemini (env)",
                          lambda p: call_gemini(p, os.environ["GEMINI_API_KEY"])))
    if os.getenv("OPENROUTER_KEY"):
        providers.append((ENV_PRIORITY, "OpenRouter (env)",
                          lambda p: call_openai_style(
                              "https://openrouter.ai/api/v1/chat/completions",
                              os.getenv("OPENROUTER_MODEL", "qwen/qwen2.5-vl-32b-instruct:free"),
                              os.environ["OPENROUTER_KEY"], p)))
    if os.getenv("GROQ_KEY"):
        providers.append((ENV_PRIORITY, "Groq (env)",
                          lambda p: call_openai_style(
                              "https://api.groq.com/openai/v1/chat/completions",
                              "meta-llama/llama-4-scout-17b-16e-instruct",
                              os.environ["GROQ_KEY"], p)))
    return providers


def _group_by_priority(providers):
    """Group providers by priority. Returns list of (priority, [(name, fn), ...])."""
    from collections import defaultdict
    groups = defaultdict(list)
    for priority, name, fn in providers:
        groups[priority].append((name, fn))
    return sorted(groups.items())


def _try_provider_parallel(name, fn, pages, on_progress=None):
    """Try a single provider. Returns (data, name) or raises."""
    if len(pages) > 2:
        return _extract_chunked(pages, fn, name, on_progress), name
    return fn(pages), name


def extract(pages: list[Path], on_progress=None) -> tuple[dict, str]:
    """Try providers grouped by priority. Within each group, split pages across providers in parallel.

    If 4 providers at same priority and 4 pages:
      Provider A → page 1, Provider B → page 2, Provider C → page 3, Provider D → page 4
    If more pages than providers, they cycle (round-robin).
    Results are merged into one.
    """
    import time as _time
    from concurrent.futures import ThreadPoolExecutor, as_completed

    providers = _providers()
    if not providers:
        raise RuntimeError("No AI providers configured. Add one in Settings.")

    errors = []
    for priority, group in _group_by_priority(providers):
        n_providers = len(group)

        if n_providers == 1 or len(pages) <= 1:
            # Single provider or single page — no splitting needed
            name, fn = group[0]
            try:
                if len(pages) > 2:
                    return _extract_chunked(pages, fn, name, on_progress), name
                return fn(pages), name
            except Exception as e:
                errors.append(f"{name}: {e}")
                continue

        # Multiple providers — split pages round-robin across all of them
        # No artificial cap — use all providers at same priority
        max_parallel = n_providers

        # Assign pages to providers round-robin
        # Track both the page Path and its original 1-indexed page number
        provider_pages = {i: [] for i in range(max_parallel)}
        provider_page_nos = {i: [] for i in range(max_parallel)}
        for page_idx, page in enumerate(pages):
            provider_idx = page_idx % max_parallel
            provider_pages[provider_idx].append(page)
            provider_page_nos[provider_idx].append(page_idx + 1)  # 1-indexed

        def _process_subset(provider_idx, name, fn, assigned_pages, page_nos, on_progress):
            """Process a subset of pages with one provider, return (data, name)."""
            if len(assigned_pages) > 2:
                result = _extract_chunked(assigned_pages, fn, name, on_progress)
                # Override page_no with actual page numbers
                for ln_idx, ln in enumerate(result.get("lines", [])):
                    # Assign page_no based on chunk position in this provider's subset
                    chunk_idx = ln_idx  # Rough — _extract_chunked sets page_no relative to subset
                    # We'll fix this: _extract_chunked sets page_no = i+1 (relative to its subset)
                    # So page_no=1 means first page in this provider's subset
                    if "page_no" in ln and ln["page_no"] and 1 <= int(ln["page_no"]) <= len(page_nos):
                        ln["page_no"] = page_nos[int(ln["page_no"]) - 1]
                return result, name
            else:
                result = fn(assigned_pages)
                # Set page_no for all lines from this provider
                for ln in (result.get("lines") or []):
                    if len(page_nos) == 1:
                        ln["page_no"] = page_nos[0]
                return result, name

        merged = {
            "phone": None, "supplier_guess": None, "bill_date": None,
            "written_total": None, "lines": [], "line_confidence": [],
        }
        partial_errors = []
        success_count = 0

        with ThreadPoolExecutor(max_workers=max_parallel) as executor:
            futures = {}
            for i, (name, fn) in enumerate(group[:max_parallel]):
                assigned = provider_pages[i]
                page_nos = provider_page_nos[i]
                if assigned:
                    futures[executor.submit(_process_subset, i, name, fn, assigned, page_nos, on_progress)] = (name, i)

            for future in as_completed(futures):
                name, prov_idx = futures[future]
                try:
                    result_data, result_name = future.result()
                    # Merge results — written_total: take the MAX (each provider may find it on their page)
                    for key in ("phone", "supplier_guess", "bill_date"):
                        if merged[key] is None and result_data.get(key) is not None:
                            merged[key] = result_data[key]
                    # For written_total: each provider may extract it from their page.
                    # Take the largest value found (it's the grand total, same on all pages).
                    wt = result_data.get("written_total")
                    if wt is not None:
                        if merged["written_total"] is None or wt > merged["written_total"]:
                            merged["written_total"] = wt
                    # Lines already have page_no set correctly by _process_subset
                    merged["lines"].extend(result_data.get("lines") or [])
                    merged["line_confidence"].extend(result_data.get("line_confidence") or [])
                    success_count += 1
                except Exception as e:
                    partial_errors.append(f"{name}: {e}")
                    errors.append(f"{name}: {e}")

        if success_count > 0:
            # At least one provider succeeded — return merged results
            if partial_errors:
                merged["_partial_errors"] = partial_errors
            # Cross-chunk validation
            _validate_merged(merged)
            return merged, " + ".join(name for name, _ in group[:success_count])

        # All providers in this group failed — try next priority group
        continue

    raise RuntimeError(" | ".join(errors) or "All providers failed.")


def _validate_merged(merged):
    """Run cross-chunk validation on merged results."""
    written = merged.get("written_total")
    if written and merged["lines"]:
        try:
            import re as _re
            line_sum = 0
            for ln in merged["lines"]:
                p = ln.get("price") or 0
                q_str = str(ln.get("qty_as_written", "0"))
                q_match = _re.search(r"\d+(?:\.\d+)?", q_str)
                q = float(q_match.group()) if q_match else 0
                line_sum += p * q
            if written > 0:
                ratio = line_sum / written
                if ratio > 3 or ratio < 0.33:
                    merged.setdefault("_partial_errors", []).append(
                        f"cross-chunk warning: sum of items ({line_sum:.0f}) is {ratio:.1f}x "
                        f"the written total ({written:.0f}) — some pages may have extraction errors"
                    )
        except Exception as _e:
            logger.warning("Silent exception in extract.py: %s", _e, exc_info=True)
CHUNK_SIZE = 1  # 1 page per request — faster, no timeouts, more API calls but each is small


def _extract_chunked(pages: list[Path], fn, provider_name: str, on_progress=None) -> dict:
    """Process pages in chunks and merge results. Retries with backoff on rate limits."""
    import time as _time
    merged = {
        "phone": None, "supplier_guess": None, "bill_date": None,
        "written_total": None, "lines": [], "line_confidence": [],
    }
    chunk_errors = []
    total_chunks = (len(pages) + CHUNK_SIZE - 1) // CHUNK_SIZE
    for i in range(0, len(pages), CHUNK_SIZE):
        chunk = pages[i:i + CHUNK_SIZE]
        chunk_idx = i // CHUNK_SIZE
        chunk_label = f"page {i+1}" if CHUNK_SIZE == 1 else f"pages {i+1}-{min(i+CHUNK_SIZE, len(pages))}"
        if on_progress:
            try:
                on_progress(chunk_label, chunk_idx + 1, total_chunks)
            except Exception as _e:
                logger.warning("Silent exception in extract.py: %s", _e, exc_info=True)
        result = None
        # Try up to 3 times per chunk, with backoff on rate limit (429) errors
        for attempt in (1, 2, 3):
            try:
                result = fn(chunk)
                break
            except Exception as e:
                err_str = str(e)
                # If rate limited (429), extract wait time and sleep
                if "429" in err_str or "rate limit" in err_str.lower():
                    import re as _re
                    # Try to extract "try again in Xs" from the error
                    wait_match = _re.search(r'try again in ([\d.]+)\s*s', err_str, _re.IGNORECASE)
                    wait_sec = float(wait_match.group(1)) if wait_match else 20.0
                    wait_sec = min(wait_sec + 2, 60)  # Add 2s buffer, cap at 60s
                    if attempt < 3:
                        if on_progress:
                            try:
                                on_progress(chunk_label, chunk_idx + 1, total_chunks)
                            except Exception as _e:
                                logger.warning("Silent exception in extract.py: %s", _e, exc_info=True)
                        _time.sleep(wait_sec)
                    else:
                        chunk_errors.append(f"{chunk_label}: rate limited after {attempt} attempts")
                else:
                    # Non-rate-limit error — retry once then give up
                    if attempt >= 2:
                        chunk_errors.append(f"{chunk_label}: {e}")
                    else:
                        _time.sleep(2)  # Brief pause before retry

        if result:
            # Merge — first non-null phone/supplier/date/total wins
            for key in ("phone", "supplier_guess", "bill_date", "written_total"):
                if merged[key] is None and result.get(key) is not None:
                    merged[key] = result[key]
            # Attach page_no to each line (1-indexed page number)
            page_no = i + 1  # Since CHUNK_SIZE=1, i is the page index
            for ln in (result.get("lines") or []):
                ln["page_no"] = page_no
            merged["lines"].extend(result.get("lines") or [])
            merged["line_confidence"].extend(result.get("line_confidence") or [])

        # Brief pause between chunks to avoid rate limits (only if more chunks remain)
        if i + CHUNK_SIZE < len(pages):
            _time.sleep(3)  # 3-second pause between pages

    if chunk_errors and not merged["lines"]:
        raise RuntimeError("All chunks failed: " + " | ".join(chunk_errors))
    # Attach partial errors as a warning field (validator can surface these)
    if chunk_errors:
        merged["_partial_errors"] = chunk_errors

    # ---- Cross-chunk validation ----
    # If we got a written_total from any chunk, verify the sum of all lines is close
    written = merged.get("written_total")
    if written and merged["lines"]:
        try:
            line_sum = 0
            for ln in merged["lines"]:
                p = ln.get("price") or 0
                q_str = str(ln.get("qty_as_written", "0"))
                import re as _re
                q_match = _re.search(r"\d+(?:\.\d+)?", q_str)
                q = float(q_match.group()) if q_match else 0
                line_sum += p * q
            if written > 0:
                ratio = line_sum / written
                if ratio > 3 or ratio < 0.33:
                    merged.setdefault("_partial_errors", []).append(
                        f"cross-chunk warning: sum of items ({line_sum:.0f}) is {ratio:.1f}x "
                        f"the written total ({written:.0f}) — some pages may have extraction errors"
                    )
        except Exception as _e:
            logger.warning("Silent exception in extract.py: %s", _e, exc_info=True)

    # v8.16.0: Add structural confidence scoring (not LLM self-assessment)
    try:
        from .extraction_confidence import compute_bill_confidence
        merged = compute_bill_confidence(merged)
    except Exception:
        pass  # Don't fail extraction if confidence scoring has a bug

    return merged


# ---------- Test connection (lightweight, no images) ----------

def test_gemini(key: str, model: str = "gemini-2.5-flash") -> dict:
    """Send a tiny text-only request to verify the key + model work.

    Returns {"ok": True, "model": model, "response": "..."} or raises RuntimeError.

    v8.5: enforces kill switch + budget (consistent with call_gemini).
    """
    _enforce_ai_guardrails("gemini")
    if not model or not model.strip():
        model = "gemini-2.5-flash"
    try:
        r = httpx.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            headers={"x-goog-api-key": key},
            json={
                "contents": [{"parts": [{"text": "Reply with exactly: OK"}]}],
                "generationConfig": {"temperature": 0, "maxOutputTokens": 100},
            },
            timeout=30,
        )
        r.raise_for_status()
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise RuntimeError(
                f"Model '{model}' not found (404). Valid models: "
                f"gemini-2.5-flash, gemini-2.0-flash, gemini-1.5-flash, gemini-1.5-pro"
            ) from e
        try:
            body = e.response.json()
            err_msg = body.get("error", {}).get("message", str(body))
        except Exception:
            err_msg = e.response.text[:200]
        raise RuntimeError(f"API error {e.response.status_code}: {err_msg}") from e
    except httpx.TimeoutException as e:
        raise RuntimeError("Request timed out (30s). Check your connection.") from e
    except httpx.RequestError as e:
        raise RuntimeError(f"Network error: {e}") from e

    data = r.json()
    candidates = data.get("candidates", [])
    if not candidates:
        raise RuntimeError(f"No candidates in response: {str(data)[:200]}")
    candidate = candidates[0]
    finish_reason = candidate.get("finishReason", "")
    # Check for safety blocks or other issues
    if finish_reason == "SAFETY":
        raise RuntimeError("Response was blocked by safety filters. Try a different model.")
    # Extract text from parts — handle missing parts gracefully
    parts = candidate.get("content", {}).get("parts", [])
    if parts:
        text = parts[0].get("text", "").strip()
    else:
        # No parts — could be MAX_TOKENS with 0 output, or empty response.
        # The key+model are valid if we got here (no API error), so treat as success.
        text = f"(connected, finishReason={finish_reason})"
    return {"ok": True, "model": model, "response": text[:100] if text else "(empty)"}


def test_openai_style(url: str, model: str, key: str) -> dict:
    """Send a tiny text-only request to verify Groq/OpenRouter key + model."""
    if not model or not model.strip():
        raise RuntimeError("No model specified")
    try:
        r = httpx.post(
            url,
            json={
                "model": model,
                "temperature": 0,
                "max_tokens": 10,
                "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
            },
            headers={"Authorization": f"Bearer {key}"},
            timeout=30,
        )
        r.raise_for_status()
    except httpx.HTTPStatusError as e:
        try:
            body = e.response.json()
            err_msg = body.get("error", {}).get("message", str(body))
        except Exception:
            err_msg = e.response.text[:200]
        raise RuntimeError(f"API error {e.response.status_code}: {err_msg}") from e
    except httpx.TimeoutException as e:
        raise RuntimeError("Request timed out (30s). Check your connection.") from e
    except httpx.RequestError as e:
        raise RuntimeError(f"Network error: {e}") from e

    try:
        text = r.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as e:
        raise RuntimeError(f"Unexpected response format: {r.text[:200]}") from e
    return {"ok": True, "model": model, "response": text.strip()[:100]}


def test_provider(provider_type: str, key: str, model: str = "") -> dict:
    """Test any provider type. Returns {"ok": True, ...} or raises RuntimeError."""
    if provider_type == "gemini":
        return test_gemini(key, model or "gemini-2.5-flash")
    elif provider_type == "groq":
        return test_openai_style(
            "https://api.groq.com/openai/v1/chat/completions",
            model or "meta-llama/llama-4-scout-17b-16e-instruct",
            key,
        )
    elif provider_type == "openrouter":
        return test_openai_style(
            "https://openrouter.ai/api/v1/chat/completions",
            model or "qwen/qwen2.5-vl-32b-instruct:free",
            key,
        )
    raise RuntimeError(f"Unknown provider type: {provider_type}")
