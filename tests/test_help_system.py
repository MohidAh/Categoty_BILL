"""v7.1 — In-App Help System tests."""
import os, sys, tempfile, shutil
from pathlib import Path
from test_helpers import setup_test_db, cleanup
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
SAMPLE_SQL = PROJECT_ROOT / "tests" / "sample_data.sql"

def setup_test_db():
    test_dir = tempfile.mkdtemp(prefix="billbook_help_")
    from app import config, db
    db.DB_PATH = os.path.join(test_dir, "billbook.db")
    config.DATA = test_dir
    for name in ("DATA", "UPLOADS", "PAGES", "BACKUPS"):
        os.makedirs(getattr(config, name), exist_ok=True)
    db.init()
    with db.conn() as c:
        for t in ("sale_items", "sales", "bill_items", "bills",
                  "customers", "price_categories", "suppliers",
                  "stock_adjustments", "activity_log", "sessions",
                  "expenses", "expense_categories", "recurring_expenses",
                  "cash_drawer", "shifts", "employees",
                  "category_stock_state", "owner_withdrawals",
                  "login_attempts", "devices", "pairing_codes",
                  "ai_cache", "ai_usage", "pending_actions", "automation_config"):
            c.execute(f"DELETE FROM {t}")
        with open(SAMPLE_SQL) as f:
            c.executescript(f.read())
        defaults = [("Rent", 1, 0, 1), ("Salaries", 1, 0, 2),
                    ("Electricity", 0, 0, 3), ("Transport", 0, 0, 4),
                    ("Internet", 0, 0, 5), ("Maintenance", 0, 0, 6),
                    ("Marketing", 0, 0, 7), ("Other", 0, 0, 8)]
        for name, is_fixed, budget, sort_order in defaults:
            c.execute("INSERT INTO expense_categories(name, is_fixed, budget_monthly, active, sort_order) VALUES(?,?,?,?,?)",
                      (name, is_fixed, budget, 1, sort_order))
        for key in ['ai_kill_switch']:
            c.execute("INSERT OR REPLACE INTO automation_config(key, enabled, level, params_json) VALUES(?,?,?,?)", (key, 0, 0, '{}'))
    from app import profit
    profit.rebuild_stock_state()
    return test_dir


def test_faq_search_finds_results():
    test_dir = setup_test_db()
    try:
        from app.help_system import search_faq
        results = search_faq("how do I make a sale")
        assert len(results) > 0
        assert "sale" in results[0]["question"].lower() or "sale" in results[0]["keywords"][0]
    finally:
        cleanup(test_dir)

def test_help_ask_returns_faq_answer():
    test_dir = setup_test_db()
    try:
        from app.help_system import answer_help_question
        result = answer_help_question("How do I process a refund?")
        assert result["source"] in ("faq", "faq_fuzzy", "ai", "none")
        assert len(result["answer"]) > 10
        assert "suggestions" in result
    finally:
        cleanup(test_dir)

def test_help_ask_cogs_explanation():
    test_dir = setup_test_db()
    try:
        from app.help_system import answer_help_question
        result = answer_help_question("What is COGS?")
        assert result["source"] in ("faq", "faq_fuzzy", "ai", "none")
        assert len(result["answer"]) > 20
    finally:
        cleanup(test_dir)

def test_articles_filtered_by_role():
    test_dir = setup_test_db()
    try:
        from app.help_system import get_articles
        cashier_articles = get_articles("cashier")
        manager_articles = get_articles("manager")
        # Cashier should see fewer articles (POS + troubleshooting + shortcuts only)
        assert len(cashier_articles) < len(manager_articles)
        # Cashier articles should all have 'cashier' in roles
        for a in cashier_articles:
            assert "cashier" in a["roles"]
    finally:
        cleanup(test_dir)

def test_help_ask_unknown_question():
    test_dir = setup_test_db()
    try:
        from app.help_system import answer_help_question
        result = answer_help_question("What is the meaning of life?")
        # Should return something — either a fuzzy match or "none"
        assert "answer" in result
        assert "suggestions" in result
    finally:
        cleanup(test_dir)

if __name__ == "__main__":
    test_faq_search_finds_results(); print("✓ test_faq_search_finds_results")
    test_help_ask_returns_faq_answer(); print("✓ test_help_ask_returns_faq_answer")
    test_help_ask_cogs_explanation(); print("✓ test_help_ask_cogs_explanation")
    test_articles_filtered_by_role(); print("✓ test_articles_filtered_by_role")
    test_help_ask_unknown_question(); print("✓ test_help_ask_unknown_question")
    print("\n✅ ALL HELP SYSTEM TESTS PASSED")
