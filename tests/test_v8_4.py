"""v8.4 — Tests for LLM agent, ZIP upload, pagination, and Linear design system."""
import os, sys, tempfile, shutil, io, zipfile, json
from pathlib import Path
from test_helpers import setup_test_db_with_password as setup_test_db, cleanup
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))




def test_agent_has_tool_descriptions():
    """Each tool must have a detailed description so the LLM can choose intelligently."""
    from app.agent import TOOL_DESCRIPTIONS, READ_TOOLS
    for tool_name in READ_TOOLS:
        assert tool_name in TOOL_DESCRIPTIONS, f"Tool '{tool_name}' missing from TOOL_DESCRIPTIONS"
        desc = TOOL_DESCRIPTIONS[tool_name]
        assert len(desc) > 20, f"Tool '{tool_name}' description too short: '{desc}'"
    print(f"  OK: All {len(READ_TOOLS)} tools have descriptions")


def test_agent_tool_schemas_have_descriptions():
    """TOOL_SCHEMAS must include descriptions for the LLM."""
    from app.agent import TOOL_SCHEMAS
    for schema in TOOL_SCHEMAS:
        fn = schema["function"]
        assert "description" in fn, f"Tool {fn['name']} missing description in schema"
        assert len(fn["description"]) > 10
    print(f"  OK: All {len(TOOL_SCHEMAS)} tool schemas have descriptions")


def test_agent_llm_fallback_to_heuristic_without_key():
    """When no Groq key is configured, agent should fall back to heuristic."""
    test_dir = setup_test_db()
    try:
        # Make sure no Groq provider is configured
        from app.db import conn
        with conn() as c:
            c.execute("DELETE FROM ai_providers WHERE provider_type='groq'")
        # Ensure env var is not set
        old_key = os.environ.pop("GROQ_KEY", None)
        old_key2 = os.environ.pop("GROQ_API_KEY", None)
        from app.agent import _get_groq_config
        key, model = _get_groq_config()
        assert key is None, f"Expected no Groq key, got {key}"
        print("  OK: No Groq key → agent falls back to heuristic")
        # Restore env
        if old_key: os.environ["GROQ_KEY"] = old_key
        if old_key2: os.environ["GROQ_API_KEY"] = old_key2
    finally:
        cleanup(test_dir)


def test_agent_margin_question_calls_tools_in_heuristic():
    """The heuristic fallback should still call tools for real questions."""
    test_dir = setup_test_db()
    try:
        from app.agent import run_agent
        r = run_agent("What is my actual overall margin?")
        assert len(r["tool_trace"]) > 0, "Margin question should call tools"
        print("  OK: Heuristic fallback calls tools for margin question")
    finally:
        cleanup(test_dir)


# ─── ZIP Upload Tests ──────────────────────────────────────────────────────

def test_zip_extraction_csv():
    """ZIP containing a CSV file should be extracted correctly."""
    test_dir = setup_test_db()
    try:
        from app.pos_import import extract_zip_contents
        csv_content = "Invoice,Date,Total\nINV-001,2026-08-10,500\n"
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("sales.csv", csv_content)
        result = extract_zip_contents(buf.getvalue())
        assert result["file_format"] == "csv"
        assert "Invoice" in result["content"]
        print("  OK: ZIP with CSV extracted")
    finally:
        cleanup(test_dir)


def test_zip_extraction_json():
    """ZIP containing a JSON file should be extracted correctly."""
    test_dir = setup_test_db()
    try:
        from app.pos_import import extract_zip_contents
        json_content = json.dumps([{"invoice": "INV-001", "total": 500}])
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("data.json", json_content)
        result = extract_zip_contents(buf.getvalue())
        assert result["file_format"] == "json"
        assert "INV-001" in result["content"]
        print("  OK: ZIP with JSON extracted")
    finally:
        cleanup(test_dir)


def test_zip_rejects_no_data_files():
    """ZIP with no data files should raise ValueError."""
    test_dir = setup_test_db()
    try:
        from app.pos_import import extract_zip_contents
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("image.png", b"\x89PNG fake")
            zf.writestr("doc.pdf", b"fake PDF")
        try:
            extract_zip_contents(buf.getvalue())
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "No data files" in str(e)
        print("  OK: ZIP with no data files rejected")
    finally:
        cleanup(test_dir)


def test_zip_rejects_invalid():
    """Invalid ZIP should raise ValueError."""
    test_dir = setup_test_db()
    try:
        from app.pos_import import extract_zip_contents
        try:
            extract_zip_contents(b"not a zip file")
            assert False, "Should have raised ValueError"
        except ValueError:
            pass
        print("  OK: Invalid ZIP rejected")
    finally:
        cleanup(test_dir)


def test_zip_api_endpoint():
    """The /api/pos-import/upload-zip endpoint should work end-to-end."""
    test_dir = setup_test_db()
    try:
        from fastapi.testclient import TestClient
        from app.main import app
        from app.security import hash_password
        from app.db import conn
        with conn() as c:
            c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('password_hash',?)",
                      (hash_password('admin123'),))
        client = TestClient(app)
        client.post('/api/login', json={"password": "admin123"})
        csv_content = "Invoice,Date,Total\nINV-001,2026-08-10,500\nINV-002,2026-08-10,300\n"
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("backup.csv", csv_content)
        r = client.post('/api/pos-import/upload-zip',
                        files={'file': ('test.zip', buf.getvalue(), 'application/zip')})
        data = r.json()
        assert r.status_code == 200
        assert "error" not in data
        assert data["total_rows"] == 2
        print("  OK: ZIP upload API endpoint works")
    finally:
        cleanup(test_dir)


# ─── Pagination Tests ──────────────────────────────────────────────────────

def test_sales_pagination():
    """Sales endpoint should support page/page_size pagination."""
    test_dir = setup_test_db()
    try:
        from fastapi.testclient import TestClient
        from app.main import app
        from app.security import hash_password
        from app.db import conn
        with conn() as c:
            c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('password_hash',?)",
                      (hash_password('admin123'),))
            # Insert some test sales
            for i in range(25):
                c.execute("INSERT INTO sales(total, payment_method, payment_status) VALUES(?,?,?)",
                          (100 + i, 'cash', 'paid'))
        client = TestClient(app)
        client.post('/api/login', json={"password": "admin123"})
        # Test page 1 with page_size=10
        r = client.get('/api/sales?page=1&page_size=10')
        data = r.json()
        assert "sales" in data
        assert data["total"] == 25
        assert data["page"] == 1
        assert data["page_size"] == 10
        assert data["pages_total"] == 3
        assert len(data["sales"]) == 10
        print("  OK: Sales pagination works (page 1)")
        # Test page 3 (last page, should have 5 items)
        r = client.get('/api/sales?page=3&page_size=10')
        data = r.json()
        assert len(data["sales"]) == 5
        print("  OK: Sales pagination works (last page)")
    finally:
        cleanup(test_dir)


def test_suppliers_pagination():
    """Suppliers endpoint should support pagination."""
    test_dir = setup_test_db()
    try:
        from fastapi.testclient import TestClient
        from app.main import app
        from app.security import hash_password
        from app.db import conn
        with conn() as c:
            c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('password_hash',?)",
                      (hash_password('admin123'),))
            for i in range(15):
                c.execute("INSERT INTO suppliers(name, phone) VALUES(?,?)",
                          (f"Supplier {i}", f"0300{i}"))
        client = TestClient(app)
        client.post('/api/login', json={"password": "admin123"})
        r = client.get('/api/suppliers?page=1&page_size=5')
        data = r.json()
        assert "suppliers" in data
        assert data["total"] == 15
        assert data["pages_total"] == 3
        assert len(data["suppliers"]) == 5
        print("  OK: Suppliers pagination works")
    finally:
        cleanup(test_dir)


def test_sales_backward_compat():
    """Sales endpoint without pagination params should return a plain list (backward compat)."""
    test_dir = setup_test_db()
    try:
        from fastapi.testclient import TestClient
        from app.main import app
        from app.security import hash_password
        from app.db import conn
        with conn() as c:
            c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('password_hash',?)",
                      (hash_password('admin123'),))
            c.execute("INSERT INTO sales(total, payment_method, payment_status) VALUES(?,?,?)",
                      (100, 'cash', 'paid'))
        client = TestClient(app)
        client.post('/api/login', json={"password": "admin123"})
        r = client.get('/api/sales?limit=10')
        data = r.json()
        # Should be a plain list, not a dict
        assert isinstance(data, list), f"Expected list, got {type(data)}"
        print("  OK: Sales backward compat (plain list) works")
    finally:
        cleanup(test_dir)


# ─── Linear Design System Tests ────────────────────────────────────────────

def test_linear_dark_is_default():
    """v8.15.0 (design.md): the default theme is now the cream canvas ('light'),
    per the design.md brand default — updated from the Linear-era dark default."""
    theme_js = (PROJECT_ROOT / "app" / "static" / "js" / "core" / "theme.js").read_text()
    utils_js = (PROJECT_ROOT / "app" / "static" / "js" / "utils.js").read_text()
    assert "theme: 'light'" in utils_js, "appearance defaults should be light (cream canvas)"
    assert "applyAppearance" in utils_js, "theme.js re-exports the shared appearance engine"
    assert "|| 'dark'" not in theme_js, "dark-default leftover should be gone from theme.js"
    print("  OK: Cream-light is the default theme (design.md)")


def test_linear_color_palette():
    """CSS variables should use Linear's color palette."""
    css = (PROJECT_ROOT / "app" / "static" / "css" / "design-system.css").read_text()
    # Linear's signature colors
    assert "#08090A" in css, "Linear primary background #08090A must be present"
    assert "#F7F8F8" in css, "Linear primary text #F7F8F8 must be present"
    assert "#5E6AD2" in css, "Linear interactive indigo #5E6AD2 must be present"
    assert "#F91880" in css, "Linear accent pink #F91880 must be present"
    assert "#00BA7C" in css, "Linear accent green #00BA7C must be present"
    print("  OK: Linear color palette in CSS")


def test_linear_pill_buttons():
    """Buttons should use pill radius (9999px)."""
    css = (PROJECT_ROOT / "app" / "static" / "css" / "design-system.css").read_text()
    # The .btn class should use --radius-pill
    btn_block = css.split(".btn {")[1].split("}")[0]
    assert "radius-pill" in btn_block or "9999px" in btn_block, \
        "Buttons must use pill radius (9999px)"
    print("  OK: Buttons are pill-shaped")


def test_linear_inter_font():
    """Inter should be the primary font (Linear uses Inter Variable)."""
    css = (PROJECT_ROOT / "app" / "static" / "css" / "design-system.css").read_text()
    assert "Inter" in css, "Inter font must be specified"
    print("  OK: Inter font is configured")


def test_linear_8px_spacing():
    """Spacing should use the 8px grid."""
    css = (PROJECT_ROOT / "app" / "static" / "css" / "design-system.css").read_text()
    assert "--space-1: 4px" in css
    assert "--space-2: 8px" in css
    assert "--space-4: 16px" in css
    assert "--space-8: 32px" in css
    print("  OK: 8px spacing grid in place")


def test_index_html_dark_default():
    """v8.15.0 (design.md): index.html pre-paint boot defaults to the cream
    canvas ('light') with coral accent — updated from the Linear-era dark default."""
    html = (PROJECT_ROOT / "app" / "static" / "index.html").read_text()
    assert "theme: 'light'" in html, "index.html appearance boot should default to light (cream)"
    assert "accent_color: '#cc785c'" in html, "index.html boot should default to coral accent"
    assert "|| 'dark'" not in html, "dark-default leftover should be gone from index.html"
    assert "#faf9f5" in html or "#181715" in html, "index.html theme-color should be cream or dark-navy"
    print("  OK: index.html defaults to cream-light + coral (design.md)")


if __name__ == "__main__":
    print("=== v8.4 LLM Agent Tests ===")
    test_agent_has_tool_descriptions()
    test_agent_tool_schemas_have_descriptions()
    test_agent_llm_fallback_to_heuristic_without_key()
    test_agent_margin_question_calls_tools_in_heuristic()

    print("\n=== v8.4 ZIP Upload Tests ===")
    test_zip_extraction_csv()
    test_zip_extraction_json()
    test_zip_rejects_no_data_files()
    test_zip_rejects_invalid()
    test_zip_api_endpoint()

    print("\n=== v8.4 Pagination Tests ===")
    test_sales_pagination()
    test_suppliers_pagination()
    test_sales_backward_compat()

    print("\n=== v8.4 Linear Design System Tests ===")
    test_linear_dark_is_default()
    test_linear_color_palette()
    test_linear_pill_buttons()
    test_linear_inter_font()
    test_linear_8px_spacing()
    test_index_html_dark_default()

    print("\n✅ ALL v8.4 TESTS PASSED")
