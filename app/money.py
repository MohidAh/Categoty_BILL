"""Phase 0 PR 1: Money rounding utilities.

Prevents floating-point precision errors in financial calculations.
SQLite stores money as REAL (float), which can produce values like
10.300000000000001. This module provides safe rounding using Decimal.

Usage:
    from app.money import money, money_d

    # At API input boundaries (before writing to DB):
    c.execute("INSERT INTO sales (total) VALUES (?)", (money(payload.total),))

    # For calculations (returns Decimal for exact arithmetic):
    total = money_d(sell_price) * money_d(qty)
    profit = total - money_d(cost)
    c.execute("INSERT INTO ... VALUES (?)", (money(profit),))
"""
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation

TWO_PLACES = Decimal("0.01")
ZERO = Decimal("0.00")


def money_d(value, default: Decimal = ZERO) -> Decimal:
    """Convert a value to a Decimal rounded to 2 places using ROUND_HALF_UP.

    This is for financial calculations where exact precision matters.

    Args:
        value: int, float, str, Decimal, or None
        default: fallback if value is None/empty/invalid (default: Decimal("0.00"))

    Returns:
        Decimal rounded to 2 decimal places

    Examples:
        >>> money_d(10.300000000000001)
        Decimal('10.30')
        >>> money_d("10.005")
        Decimal('10.01')
        >>> money_d("10.004")
        Decimal('10.00')
        >>> money_d(None)
        Decimal('0.00')
        >>> money_d("abc")
        Decimal('0.00')
    """
    if value is None or value == "":
        return default.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

    try:
        d = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return default.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

    if not d.is_finite():
        return default.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

    return d.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def money(value, default: float = 0.0) -> float:
    """Convert a value to a float rounded to 2 decimal places.

    Use this at API boundaries and before writing money values
    to SQLite REAL columns. Returns float (for DB storage);
    use money_d() for calculations (returns Decimal).

    Args:
        value: int, float, str, Decimal, or None
        default: fallback if value is None/empty/invalid (default: 0.0)

    Returns:
        float rounded to 2 decimal places

    Examples:
        >>> money(10.300000000000001)
        10.3
        >>> money("10.005")
        10.01
        >>> money(None)
        0.0
    """
    return float(money_d(value, Decimal(str(default))))
