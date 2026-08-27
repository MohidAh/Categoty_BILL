"""v8.1 Phase 3 — QR-Code Pairing tests."""
import os, sys, tempfile, shutil, json
from pathlib import Path
from test_helpers import setup_test_db, cleanup
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def setup_test_db():
    test_dir = tempfile.mkdtemp(prefix="billbook_v81_p3_")
    from app import config, db
    db.DB_PATH = os.path.join(test_dir, "billbook.db")
    config.DATA = test_dir
    for name in ("DATA", "UPLOADS", "PAGES", "BACKUPS"):
        os.makedirs(getattr(config, name), exist_ok=True)
    db.init()
    with db.conn() as c:
        for t in ("pairing_codes", "devices", "branches", "branch_pairing_codes"):
            c.execute(f"DELETE FROM {t}")
    return test_dir



class FakeRequest:
    def __init__(self, base_url="http://127.0.0.1:8000/"):
        self.base_url = base_url


async def _read_body(response):
    """Read the body from a StreamingResponse."""
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk)
    return b"".join(chunks)


def _get_body(response):
    """Sync wrapper to read StreamingResponse body."""
    import asyncio
    return asyncio.run(_read_body(response))


def test_device_qr_returns_png():
    """GET /api/devices/qr returns a PNG image."""
    test_dir = setup_test_db()
    try:
        from app.routers.auth import generate_device_qr
        r = generate_device_qr(FakeRequest(), role="cashier")
        assert r.media_type == "image/png"
        body = _get_body(r)
        assert body[:4] == b'\x89PNG', "not a PNG"
        assert "x-pairing-code" in {k.lower() for k in r.headers.keys()}
        assert "x-server-url" in {k.lower() for k in r.headers.keys()}
    finally:
        cleanup(test_dir)


def test_device_qr_payload_decodes():
    """The QR payload decodes to valid pairing code + server URL + role."""
    test_dir = setup_test_db()
    try:
        from app.routers.auth import generate_device_qr
        r = generate_device_qr(FakeRequest("http://192.168.1.100:8000/"), role="manager")
        body = _get_body(r)
        pairing_code = r.headers.get("x-pairing-code")
        server_url = r.headers.get("x-server-url")
        role = r.headers.get("x-role")
        assert pairing_code and len(pairing_code) == 6, f"bad code: {pairing_code}"
        assert server_url == "http://192.168.1.100:8000"
        assert role == "manager"
        from app import db
        with db.conn() as c:
            row = c.execute("SELECT * FROM pairing_codes WHERE code=?", (pairing_code,)).fetchone()
        assert row is not None
        assert row["role"] == "manager"
    finally:
        cleanup(test_dir)


def test_device_qr_invalid_role():
    """GET /api/devices/qr with invalid role returns 400."""
    test_dir = setup_test_db()
    try:
        from app.routers.auth import generate_device_qr
        from fastapi import HTTPException
        try:
            generate_device_qr(FakeRequest(), role="invalid")
            assert False, "should raise 400"
        except HTTPException as e:
            assert e.status_code == 400
    finally:
        cleanup(test_dir)


def test_branch_qr_returns_png():
    """GET /api/hq/branches/qr returns a PNG image."""
    test_dir = setup_test_db()
    try:
        from app.routers.hq import generate_branch_qr
        r = generate_branch_qr(FakeRequest("http://hq.trycloudflare.com/"))
        assert r.media_type == "image/png"
        body = _get_body(r)
        assert body[:4] == b'\x89PNG'
        reg_code = r.headers.get("x-registration-code")
        hq_url = r.headers.get("x-hq-url")
        assert reg_code and len(reg_code) == 6
        assert hq_url == "http://hq.trycloudflare.com"
    finally:
        cleanup(test_dir)


def test_branch_qr_code_stored():
    """Branch QR generates a code that's stored in branch_pairing_codes."""
    test_dir = setup_test_db()
    try:
        from app.routers.hq import generate_branch_qr
        from app import db
        r = generate_branch_qr(FakeRequest())
        reg_code = r.headers.get("x-registration-code")
        with db.conn() as c:
            row = c.execute("SELECT * FROM branch_pairing_codes WHERE code=?", (reg_code,)).fetchone()
        assert row is not None
        assert row["used"] == 0
    finally:
        cleanup(test_dir)


def test_existing_6_digit_flow_still_works():
    """The existing 6-digit pairing flow (without QR) still works."""
    test_dir = setup_test_db()
    try:
        from app.routers.auth import generate_pairing_code
        r = generate_pairing_code(FakeRequest(), role="cashier")
        assert r["code"] and len(r["code"]) == 6
        assert r["role"] == "cashier"
        assert r["expires_in"] == 300
    finally:
        cleanup(test_dir)


if __name__ == "__main__":
    test_device_qr_returns_png(); print("OK device QR returns PNG")
    test_device_qr_payload_decodes(); print("OK device QR payload decodes")
    test_device_qr_invalid_role(); print("OK device QR invalid role 400")
    test_branch_qr_returns_png(); print("OK branch QR returns PNG")
    test_branch_qr_code_stored(); print("OK branch QR code stored")
    test_existing_6_digit_flow_still_works(); print("OK existing 6-digit flow still works")
    print("\nALL v8.1 PHASE 3 TESTS PASSED")
