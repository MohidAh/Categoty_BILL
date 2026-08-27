"""v8.5 hardening smoke tests — verify the changes from the spec refactor.

Run with: python tests/test_v8_5_hardening.py
"""
import os
import sys
import tempfile
import shutil
from pathlib import Path

# Ensure we import from the project root
PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

# Use a temp data dir so we don't touch any real DB
TMP = Path(tempfile.mkdtemp(prefix="bb_v85_"))
os.environ["BILLBOOK_DATA_DIR"] = str(TMP)

# Mutate config.DATA before importing db (db.py imports DATA at module load)
from app import config as _config
_config.DATA = TMP
from app import db as _db
_db.DB_PATH = TMP / "billbook.db"

# Now we can import the rest
from app import db
from app import profit_engine as pe
from app import shop
from app import crypto


PASS = 0
FAIL = 0


def check(label, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label}  {detail}")


def test_db_init_seeds_defaults():
    print("\n=== Test 1: db.init() seeds v8.5 default settings ===")
    db.init()
    expected_keys = [
        "password_hash", "loyalty_points_per_rs", "loyalty_rate",
        "tax_rate", "tax_inclusive", "appearance_theme",
        "stock_state_dirty", "crypto_salt",
        "max_ai_calls_per_day_groq", "max_ai_calls_per_day_gemini",
        "max_discount_pct_without_pin", "require_pin_for_refund",
        "business_reserve_pct", "stock_reserve_target_days",
        "margin_protection_target",
    ]
    for k in expected_keys:
        row = db.get_setting(k, "__MISSING__")
        check(f"setting '{k}' seeded", row != "__MISSING__", f"got '{row}'")

    # crypto_salt must be 16 bytes base64 (24 chars)
    salt = db.get_setting("crypto_salt", "")
    check("crypto_salt is base64-encoded 16 bytes",
          len(salt) == 24, f"len={len(salt)}")


def test_weighted_average():
    print("\n=== Test 2: Weighted-average cost engine ===")
    # Re-init to clear state
    db.init()
    # Insert a category
    with db.conn() as c:
        c.execute("DELETE FROM category_stock_state")
        c.execute("DELETE FROM price_categories")
        c.execute("INSERT INTO price_categories(id, name, code, sell_price, color, sort_order, active) "
                  "VALUES(1, 'Test', 'A', 250, '#3b82f6', 1, 1)")

    # Buy 100 @ Rs 100
    pe.apply_purchase_to_state(1, 100, 100.0)
    st = pe.get_category_stock_state(1)[0]
    check("after buy 100@100: qty=100", abs(st["current_qty"] - 100) < 0.01, f"got {st['current_qty']}")
    check("after buy 100@100: value=10000", abs(st["current_value"] - 10000) < 0.01, f"got {st['current_value']}")
    check("after buy 100@100: avg=100", abs(st["current_avg_cost"] - 100) < 0.01, f"got {st['current_avg_cost']}")

    # Buy 50 @ Rs 150
    pe.apply_purchase_to_state(1, 50, 150.0)
    st = pe.get_category_stock_state(1)[0]
    check("after buy 50@150: qty=150", abs(st["current_qty"] - 150) < 0.01, f"got {st['current_qty']}")
    check("after buy 50@150: value=17500", abs(st["current_value"] - 17500) < 0.01, f"got {st['current_value']}")
    check("after buy 50@150: avg=116.67", abs(st["current_avg_cost"] - 116.67) < 0.01, f"got {st['current_avg_cost']}")

    # Sell 10 — avg stays unchanged
    pe.apply_sale_to_state(1, 10)
    st = pe.get_category_stock_state(1)[0]
    check("after sell 10: qty=140", abs(st["current_qty"] - 140) < 0.01, f"got {st['current_qty']}")
    # cogs = 10 × 116.67 = 1166.70 → new_value = 17500 - 1166.70 = 16333.30
    check("after sell 10: value=16333.30", abs(st["current_value"] - 16333.30) < 1.0, f"got {st['current_value']}")
    check("after sell 10: avg=116.67 (unchanged)", abs(st["current_avg_cost"] - 116.67) < 0.01, f"got {st['current_avg_cost']}")

    # get_inventory returns avg 116.67 (single source of truth)
    inv = shop.get_inventory()
    item = inv[0]
    check("get_inventory returns avg_cost=116.67", abs(item["avg_cost"] - 116.67) < 0.01, f"got {item['avg_cost']}")
    check("get_inventory returns stock=140", abs(item["stock"] - 140) < 0.01, f"got {item['stock']}")
    check("get_inventory has no 'purchased' key (legacy field removed)",
          "purchased" not in item, f"keys: {list(item.keys())}")


def test_cash_drawer_cash_in_out():
    print("\n=== Test 3: Cash drawer includes cash_in/cash_out ===")
    db.init()
    # Clear today's drawer
    with db.conn() as c:
        c.execute("DELETE FROM cash_drawer WHERE date(created_at)=date('now','localtime')")

    # Open with 5000
    shop.open_cash_drawer(5000.0)
    # cash_in 1000
    with db.conn() as c:
        c.execute("INSERT INTO cash_drawer(type, amount, description) VALUES('cash_in', 1000, 'Float top-up')")
    # expense 500
    with db.conn() as c:
        c.execute("INSERT INTO cash_drawer(type, amount, description) VALUES('expense', 500, 'Tea')")
    # cash_out 200
    with db.conn() as c:
        c.execute("INSERT INTO cash_drawer(type, amount, description) VALUES('cash_out', 200, 'Petty cash')")

    # Close with 5300 (expected)
    result = shop.close_cash_drawer(5300.0)
    expected = 5000 + 1000 - 500 - 200  # = 5300
    check(f"expected_cash = {expected}", abs(result["expected_cash"] - expected) < 0.01,
          f"got {result['expected_cash']}")
    check(f"difference = 0", abs(result["difference"]) < 0.01,
          f"got {result['difference']}")
    check("cash_in includes manual float",
          abs(result["cash_in_manual"] - 1000) < 0.01, f"got {result['cash_in_manual']}")
    check("cash_out includes manual withdrawal",
          abs(result["cash_out_manual"] - 200) < 0.01, f"got {result['cash_out_manual']}")


def test_loyalty_points_per_rs():
    print("\n=== Test 4: Loyalty points read from settings ===")
    db.init()
    # Set loyalty_points_per_rs to 50 (1 pt per Rs 50)
    db.set_setting("loyalty_points_per_rs", "50")
    # Create a customer
    with db.conn() as c:
        c.execute("DELETE FROM customers")
        cust_id = c.execute("INSERT INTO customers(name, phone) VALUES('Test', '0300')").lastrowid

    # Spend Rs 200 → at 1 pt / Rs 50, that's 4 points
    shop.update_customer_stats(cust_id, 200.0, is_credit=False)
    with db.conn() as c:
        row = c.execute("SELECT loyalty_points FROM customers WHERE id=?", (cust_id,)).fetchone()
    check("Rs 200 spend at 1pt/Rs50 → 4 points", row["loyalty_points"] == 4,
          f"got {row['loyalty_points']}")

    # Now change to 100 (default) and verify
    db.set_setting("loyalty_points_per_rs", "100")
    shop.update_customer_stats(cust_id, 200.0, is_credit=False)
    with db.conn() as c:
        row = c.execute("SELECT loyalty_points FROM customers WHERE id=?", (cust_id,)).fetchone()
    check("Rs 200 spend at 1pt/Rs100 → 2 more points (total 6)", row["loyalty_points"] == 6,
          f"got {row['loyalty_points']}")


def test_crypto_roundtrip():
    print("\n=== Test 5: API key encryption round-trip ===")
    db.init()
    # Set a password hash so crypto can derive a key
    from app.security import hash_password
    db.set_setting("password_hash", hash_password("test-password-123"))

    plaintext = "sk-groq-abc123def456ghi789jkl012mno345pqr678stu901vwx234yz"
    enc = crypto.encrypt_api_key(plaintext)
    check("ciphertext != plaintext", enc != plaintext)
    check("ciphertext has Fernet prefix", enc.startswith("gAAAAA"), f"got {enc[:10]}...")
    dec = crypto.decrypt_api_key(enc)
    check("decrypted == original", dec == plaintext)
    # Plaintext detection
    dec2 = crypto.decrypt_api_key("not-encrypted-at-all")
    check("plaintext passed through unchanged", dec2 == "not-encrypted-at-all")


def test_verify_manager_pin_password_fallback():
    print("\n=== Test 6: verify_manager_pin accepts main password fallback ===")
    db.init()
    with db.conn() as c:
        c.execute("DELETE FROM employees WHERE role IN ('manager','admin')")
    from app.security import hash_password
    db.set_setting("password_hash", hash_password("owner-secret-pw"))

    # Wrong pin
    res = shop.verify_manager_pin("wrong")
    check("wrong pin returns None", res is None)

    # Main password fallback
    res = shop.verify_manager_pin("owner-secret-pw")
    check("main password returns dict (fallback)", res is not None)
    check("fallback marks _via='password'",
          res and res.get("_via") == "password", f"got {res}")

    # Boolean wrapper
    check("verify_manager_pin_bool True for password",
          shop.verify_manager_pin_bool("owner-secret-pw"))
    check("verify_manager_pin_bool False for wrong",
          not shop.verify_manager_pin_bool("wrong"))


def test_ai_budget_guardrail():
    print("\n=== Test 7: AI budget guardrail raises on kill switch ===")
    db.init()
    # Enable kill switch
    with db.conn() as c:
        c.execute("DELETE FROM automation_config WHERE key='ai_kill_switch'")
        c.execute("INSERT INTO automation_config(key, enabled, level, params_json) "
                  "VALUES('ai_kill_switch', 1, 'L1', '{}')")
    from app.extract import _enforce_ai_guardrails
    try:
        _enforce_ai_guardrails("gemini")
        check("kill switch raises RuntimeError", False, "no exception raised")
    except RuntimeError as e:
        check("kill switch raises RuntimeError", "AI is disabled" in str(e), str(e))

    # Disable kill switch — should pass
    with db.conn() as c:
        c.execute("UPDATE automation_config SET enabled=0 WHERE key='ai_kill_switch'")
    try:
        _enforce_ai_guardrails("gemini")
        check("guardrail passes when kill switch off", True)
    except RuntimeError as e:
        check("guardrail passes when kill switch off", False, str(e))


def test_dead_code_scan():
    print("\n=== Test 8: Dead-code scan (SESSIONS dict etc.) ===")
    # SESSIONS = {} dict in security.py? (already removed long ago)
    security_path = PROJ / "app" / "security.py"
    content = security_path.read_text()
    check("security.py has no in-memory SESSIONS dict",
          "SESSIONS = {}" not in content and "SESSIONS={}" not in content)
    check("security.py uses SQLite sessions table",
          "FROM sessions" in content)


def main():
    try:
        test_db_init_seeds_defaults()
        test_weighted_average()
        test_cash_drawer_cash_in_out()
        test_loyalty_points_per_rs()
        test_crypto_roundtrip()
        test_verify_manager_pin_password_fallback()
        test_ai_budget_guardrail()
        test_dead_code_scan()
    finally:
        # Cleanup
        try:
            shutil.rmtree(TMP)
        except Exception:
            pass
    print(f"\n{'='*60}")
    print(f"Results: {PASS} passed, {FAIL} failed")
    print('='*60)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
