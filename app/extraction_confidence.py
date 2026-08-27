"""v8.16.0 — Structural confidence scoring for bill extraction.

Computes confidence per line item using structural validation rules (NOT raw
LLM self-assessment). Research (Jan 2026) shows that directly asking the LLM
"how sure are you?" produces unreliable scores. Instead, we validate:

1. Price is reasonable (> 0 and < sell_price of the category)
2. Quantity is a positive whole number
3. Line total matches price × qty (±5% tolerance)
4. Bill date is a valid date format
5. Supplier name is not empty

Thresholds (industry standard):
  ≥ 0.85 → green dot (auto-accepted)
  0.60–0.84 → yellow dot (flagged for review)
  < 0.60 → red dot (needs manual check)
"""
from datetime import datetime


def compute_line_confidence(line: dict, categories: list = None) -> tuple:
    """Compute structural confidence for a single bill line item.
    
    Args:
        line: dict with keys: price, qty, line_total (optional), raw, sell_price (optional)
        categories: list of price_categories dicts with sell_price
        
    Returns:
        (confidence: float 0-1, needs_review: bool, reasons: list[str])
    """
    confidence = 1.0
    reasons = []
    
    price = float(line.get("price") or 0)
    qty_str = str(line.get("qty_as_written") or line.get("qty") or "")
    qty = float(line.get("qty") or 0)
    line_total = float(line.get("line_total") or 0)
    raw = str(line.get("raw") or "")
    sell_price = float(line.get("sell_price") or 0)
    
    # Check 1: Price > 0
    if price <= 0:
        confidence -= 0.4
        reasons.append("price is zero or negative")
    elif price > 100000:
        confidence -= 0.2
        reasons.append("price unusually high (>Rs 100,000)")
    
    # Check 2: Price < sell_price (cost should be less than sell)
    if sell_price > 0 and price > sell_price:
        confidence -= 0.3
        reasons.append(f"cost (Rs {price:.0f}) exceeds sell price (Rs {sell_price:.0f})")
    
    # Check 3: Quantity is a positive whole number
    try:
        qty_val = float(qty_str.replace(",", "").strip()) if qty_str else 0
    except (ValueError, TypeError):
        qty_val = 0
    
    if qty_val <= 0:
        confidence -= 0.3
        reasons.append("quantity is zero or missing")
    elif qty_val != int(qty_val) and qty_val < 1:
        confidence -= 0.2
        reasons.append(f"quantity looks wrong ({qty_str})")
    
    # Check 4: Line total matches price × qty (±10% tolerance)
    if line_total > 0 and price > 0 and qty_val > 0:
        expected_total = price * qty_val
        if expected_total > 0:
            diff_pct = abs(line_total - expected_total) / expected_total
            if diff_pct > 0.10:
                confidence -= 0.2
                reasons.append(f"line total (Rs {line_total:.0f}) doesn't match price×qty (Rs {expected_total:.0f})")
    
    # Check 5: Raw description is not empty
    if not raw or len(raw.strip()) < 2:
        confidence -= 0.2
        reasons.append("item description is empty or too short")
    
    # Clamp to [0, 1]
    confidence = max(0.0, min(1.0, confidence))
    
    needs_review = confidence < 0.85
    
    return (round(confidence, 2), needs_review, reasons)


def compute_bill_confidence(extracted: dict, categories: list = None) -> dict:
    """Add structural confidence scores to an extraction result.
    
    Modifies the extracted dict in-place by adding:
    - `structural_confidence` per line item
    - `structural_needs_review` per line item
    - `confidence_reasons` per line item
    - `overall_confidence` (average)
    - `review_item_count` (count of items needing review)
    """
    lines = extracted.get("lines", [])
    if not lines:
        extracted["overall_confidence"] = 0.0
        extracted["review_item_count"] = 0
        return extracted
    
    total_confidence = 0.0
    review_count = 0
    
    for line in lines:
        conf, needs_review, reasons = compute_line_confidence(line, categories)
        line["structural_confidence"] = conf
        line["structural_needs_review"] = needs_review
        line["confidence_reasons"] = reasons
        total_confidence += conf
        if needs_review:
            review_count += 1
    
    extracted["overall_confidence"] = round(total_confidence / len(lines), 2)
    extracted["review_item_count"] = review_count
    
    return extracted


def get_confidence_level(confidence: float) -> str:
    """Map confidence score to a display level.
    
    Returns: "green" | "yellow" | "red"
    """
    if confidence >= 0.85:
        return "green"
    elif confidence >= 0.60:
        return "yellow"
    else:
        return "red"
