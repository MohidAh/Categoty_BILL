"""Phase 0 PR 1: Tests for money() and money_d() rounding utilities.

Verifies:
- Float noise is eliminated (10.300000000000001 → 10.30)
- Half-up rounding works (10.005 → 10.01, 10.004 → 10.00)
- None and empty strings return default (0.00)
- Invalid input returns default safely
- money() returns float, money_d() returns Decimal
- Large numbers don't lose precision
- Negative numbers round correctly
"""
from decimal import Decimal

from app.money import money, money_d


# ─── money(): float output ────────────────────────────────────────────────────

def test_money_eliminates_float_noise():
    """10.300000000000001 should become 10.30"""
    assert money(10.300000000000001) == 10.30


def test_money_rounds_half_up():
    """10.005 should round up to 10.01 (ROUND_HALF_UP)"""
    assert money("10.005") == 10.01
    assert money("10.015") == 10.02
    assert money("10.025") == 10.03


def test_money_rounds_down_correctly():
    """10.004 should round down to 10.00"""
    assert money("10.004") == 10.00
    assert money("10.014") == 10.01
    assert money("10.024") == 10.02


def test_money_handles_none():
    assert money(None) == 0.0


def test_money_handles_empty_string():
    assert money("") == 0.0


def test_money_handles_int():
    assert money(250) == 250.0


def test_money_handles_negative():
    assert money(-10.005) == -10.01
    assert money("-10.004") == -10.00


def test_money_handles_large_number():
    assert money(1441370.00) == 1441370.0
    assert money("4222844.00") == 4222844.0


def test_money_with_custom_default():
    assert money(None, default=-1.0) == -1.0


# ─── money_d(): Decimal output ─────────────────────────────────────────────────

def test_money_d_returns_decimal():
    result = money_d("15.999")
    assert isinstance(result, Decimal)
    assert result == Decimal("16.00")


def test_money_d_handles_float_noise():
    assert money_d(10.300000000000001) == Decimal("10.30")


def test_money_d_rounds_half_up():
    assert money_d("10.005") == Decimal("10.01")
    assert money_d("10.004") == Decimal("10.00")


def test_money_d_handles_none():
    assert money_d(None) == Decimal("0.00")


def test_money_d_handles_empty_string():
    assert money_d("") == Decimal("0.00")


def test_money_d_handles_invalid_string():
    assert money_d("abc") == Decimal("0.00")
    assert money_d("nan") == Decimal("0.00")
    assert money_d("inf") == Decimal("0.00")


def test_money_d_with_custom_default():
    assert money_d(None, default=Decimal("99.99")) == Decimal("99.99")
    assert money_d("abc", default=Decimal("99.99")) == Decimal("99.99")


# ─── Consistency tests ────────────────────────────────────────────────────────

def test_money_and_money_d_produce_same_value():
    """money(x) should equal float(money_d(x)) for all valid inputs"""
    test_values = [0, 1, 10.5, "10.005", 999.999, -50.5, None, ""]
    for v in test_values:
        assert money(v) == float(money_d(v))


def test_money_d_addition_is_exact():
    """Decimal addition should not produce float noise"""
    a = money_d("10.01")
    b = money_d("20.02")
    result = a + b
    assert result == Decimal("30.03")
