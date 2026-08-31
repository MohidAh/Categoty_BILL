"""v8.19: Licensing — one setup = one license.

Covers:
- Setup ID format + stability on the same machine
- License key round-trip, paste normalization, tamper/wrong-machine/expiry rejection
- activate() persistence + license_state() display fields
- The "copied database" attack: a stored license from another machine locks the app
- Live expiry: a license that expires while the app runs locks it (cache re-check)
- Middleware enforcement: pages redirect to /license, API returns 403
  license_required, public probes stay open
- /api/setup + /api/setup/wizard refuse to complete without a license
- Full happy path: activate -> setup -> login -> API access
- The owner tool script end-to-end (--init + issue + ledger)
"""
import base64
import csv
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import conftest
from app import db, licensing

PROJ = Path(__file__).resolve().parent.parent

# ─── Test keypair (independent of the production key embedded in the app) ───
_test_key = Ed25519PrivateKey.generate()
TEST_PRIV_PEM = _test_key.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
)
TEST_PUB_B64 = base64.b64encode(
    _test_key.public_key().public_bytes(serialization.Encoding.Raw,
                                        serialization.PublicFormat.Raw)
).decode()


@pytest.fixture()
def real_gate(monkeypatch):
    """Restore the REAL is_activated() (conftest pins it True for the legacy
    suite) and inject the test keypair so licenses can be minted in-process."""
    monkeypatch.setattr(licensing, "is_activated", conftest._REAL_IS_ACTIVATED)
    monkeypatch.setattr(licensing, "_PUBLIC_KEY_B64", TEST_PUB_B64)
    licensing._reset_cache()
    yield
    licensing._reset_cache()


def mint(sid=None, no=1, iat=None, exp=None):
    """Mint a license bound to this machine's Setup ID (by default)."""
    return licensing.make_license_key(
        TEST_PRIV_PEM, sid or licensing.setup_id(), no,
        iat if iat is not None else int(time.time()), exp,
    )


# ═══════════════════════════════════════════════════════════════════════════
# Unit level
# ═══════════════════════════════════════════════════════════════════════════

def test_setup_id_format_and_stability():
    sid = licensing.setup_id()
    assert re.fullmatch(r"[0-9A-F]{4}(-[0-9A-F]{4}){3}", sid), sid
    assert licensing.setup_id() == sid  # stable within the process


def test_license_round_trip(real_gate):
    key = mint(no=42, iat=1700000000, exp=None)
    ok, payload, reason = licensing.verify_license_key(key)
    assert ok, reason
    assert payload["no"] == 42
    assert payload["iat"] == 1700000000
    assert payload["exp"] is None  # perpetual
    assert reason == ""


def test_license_time_limited_round_trip(real_gate):
    exp = int(time.time()) + 86400
    key = mint(no=2, exp=exp)
    ok, payload, _ = licensing.verify_license_key(key)
    assert ok and payload["exp"] == exp


def test_paste_normalization(real_gate):
    key = mint(no=3)
    # WhatsApp-style wrapping + stray spaces + lowercase prefix + padding
    variants = [
        key,
        "bbl1." + key[5:30] + "\n" + key[30:70] + "\n" + key[70:],
        " " + key + " ",
        key + "==",
        "BBL1-" + key[5:],
    ]
    for v in variants:
        ok, payload, reason = licensing.verify_license_key(v)
        assert ok, f"variant {v[:20]!r}… rejected: {reason}"
        assert payload["no"] == 3


def test_wrong_machine_rejected(real_gate):
    other = "FFFF" + "0" * 12
    key = mint(sid=other, no=4)
    ok, payload, reason = licensing.verify_license_key(key)
    assert not ok
    assert reason == licensing.R_MACHINE
    assert payload["no"] == 4  # info kept for diagnostics


def test_expired_rejected(real_gate):
    key = mint(no=5, iat=1000, exp=2000)
    ok, _, reason = licensing.verify_license_key(key)
    assert not ok and reason == licensing.R_EXPIRED


def test_tampered_and_garbage_rejected(real_gate):
    key = mint(no=6)
    # Flip a character in the MIDDLE of the key (flipping the final base64
    # char can decode to identical bytes — only its top 2 bits are real).
    flipped = key[:60] + ("A" if key[60] != "A" else "B") + key[61:]
    assert licensing.verify_license_key(flipped)[2] == licensing.R_INVALID
    assert licensing.verify_license_key("BBL1.AAAAAAAA")[2] == licensing.R_INVALID
    assert licensing.verify_license_key("")[2] == licensing.R_INVALID
    assert licensing.verify_license_key("not-a-key-at-all")[2] == licensing.R_INVALID


def test_forged_signature_rejected(real_gate):
    # Signed by a DIFFERENT private key than the embedded public key.
    impostor = Ed25519PrivateKey.generate()
    pem = impostor.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    key = licensing.make_license_key(pem, licensing.setup_id(), 7,
                                     int(time.time()), None)
    ok, _, reason = licensing.verify_license_key(key)
    assert not ok and reason == licensing.R_INVALID


def test_make_license_key_rejects_bad_setup_id(real_gate):
    with pytest.raises(ValueError):
        licensing.make_license_key(TEST_PRIV_PEM, "TOOSHORT", 1, 1, None)


# ═══════════════════════════════════════════════════════════════════════════
# Activation state level
# ═══════════════════════════════════════════════════════════════════════════

def test_activate_persists_and_reports(tmp_db_path, real_gate):
    key = mint(no=11)
    ok, payload, reason = licensing.activate(key)
    assert ok, reason
    assert db.get_setting("license_key", "") == key
    st = licensing.license_state()
    assert st["activated"] is True
    assert st["reason"] is None
    assert st["license"]["no"] == 11
    assert st["license"]["perpetual"] is True
    assert st["setup_id"] == licensing.setup_id()


def test_activate_wrong_machine_fails(tmp_db_path, real_gate):
    other = "ABCD" + "1" * 12
    key = mint(sid=other, no=12)
    ok, _, reason = licensing.activate(key)
    assert not ok and reason == licensing.R_MACHINE
    assert db.get_setting("license_key", "") == ""  # nothing stored
    assert licensing.license_state()["activated"] is False


def test_copied_database_locks_on_new_machine(tmp_db_path, real_gate, monkeypatch):
    """The leak scenario: an activated data folder copied to another PC.

    The stored license key verifies cryptographically (the owner really
    signed it) but it was issued for a different Setup ID — the app must
    stay locked."""
    key = mint(no=13)
    ok, _, _ = licensing.activate(key)
    assert ok
    assert licensing.license_state()["activated"] is True
    # Machine changes -> fingerprint changes -> Setup ID changes.
    monkeypatch.setattr(licensing, "setup_id", lambda: "AAAA-BBBB-CCCC-DDDD")
    licensing._reset_cache()
    st = licensing.license_state()
    assert st["activated"] is False
    assert st["reason"] == licensing.R_MACHINE
    assert licensing.is_activated() is False


def test_live_expiry_locks_running_app(tmp_db_path, real_gate, monkeypatch):
    """A license valid at activation time that expires later must lock the
    running app (the signature cache must not keep it alive)."""
    soon = int(time.time()) + 2
    key = mint(no=14, exp=soon)
    ok, _, _ = licensing.activate(key)
    assert ok
    assert licensing.license_state()["activated"] is True
    # Travel past the expiry without re-verifying the signature.
    monkeypatch.setattr(licensing.time, "time", lambda: soon + 10)
    st = licensing.license_state()
    assert st["activated"] is False
    assert st["reason"] == licensing.R_EXPIRED
    assert st["license"]["no"] == 14  # display info kept for the lock screen


def test_state_missing_when_never_activated(tmp_db_path, real_gate):
    st = licensing.license_state()
    assert st["activated"] is False
    assert st["reason"] == licensing.R_MISSING
    assert st["license"] is None
    assert st["setup_id"] == licensing.setup_id()


def test_is_activated_fails_closed(tmp_db_path, real_gate, monkeypatch):
    """Broken internals (e.g. settings table unavailable) must LOCK, not open."""
    def boom():
        raise RuntimeError("db unavailable")
    monkeypatch.setattr(licensing, "license_state", boom)
    assert licensing.is_activated() is False


# ═══════════════════════════════════════════════════════════════════════════
# API + middleware level (real enforcement path)
# ═══════════════════════════════════════════════════════════════════════════

def test_api_locked_without_license(client, real_gate):
    r = client.get("/api/bills")
    assert r.status_code == 403
    assert r.json()["code"] == "license_required"
    # Public probes stay open (Tauri sidecar / watchdogs poll these).
    assert client.get("/api/version").status_code == 200
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/csrf-token").status_code in (200, 401)


def test_license_status_open_and_shows_setup_id(client, real_gate):
    r = client.get("/api/license/status")
    assert r.status_code == 200
    body = r.json()
    assert body["required"] is True
    assert body["activated"] is False
    assert re.fullmatch(r"[0-9A-F]{4}(-[0-9A-F]{4}){3}", body["setup_id"])


def test_pages_redirect_to_license(client, real_gate):
    r = client.get("/", follow_redirects=False)
    assert r.status_code in (307, 302)
    assert r.headers["location"].endswith("/license")
    # The lock screen, login and wizard pages themselves stay reachable
    # (they route the user on to /license or the wizard license step).
    for path in ("/license", "/login", "/setup-wizard"):
        assert client.get(path, follow_redirects=False).status_code == 200


def test_login_blocked_while_locked(client, real_gate):
    r = client.post("/api/login", json={"password": "whatever123"})
    assert r.status_code == 403
    assert r.json()["code"] == "license_required"


def test_setup_endpoints_blocked_without_license(client, real_gate):
    r = client.post("/api/setup", json={"password": "testpass1234"})
    assert r.status_code == 403
    assert r.json()["code"] == "license_required"
    r = client.post("/api/setup/wizard",
                    json={"password": "testpass1234", "business_type": "retail"})
    assert r.status_code == 403
    assert r.json()["code"] == "license_required"
    # ...and nothing was persisted:
    assert db.get_setting("password_hash", "") == ""


def test_activate_endpoint_rejections(client, real_gate):
    r = client.post("/api/license/activate", json={"license_key": "garbage"})
    assert r.status_code == 403
    assert r.json()["code"] == licensing.R_INVALID
    other = mint(sid="0123" + "A" * 12, no=20)
    r = client.post("/api/license/activate", json={"license_key": other})
    assert r.status_code == 403
    assert r.json()["code"] == licensing.R_MACHINE
    assert "different machine" in r.json()["error"].lower()


def test_full_happy_path_activate_setup_login(client, real_gate):
    # 1. Activate the license for THIS machine.
    key = mint(no=30)
    r = client.post("/api/license/activate", json={"license_key": key})
    assert r.status_code == 200, r.text
    assert r.json()["license"]["no"] == 30

    # 2. License gate open, but auth still required.
    assert client.get("/api/bills").status_code == 401

    # 3. Setup now works.
    r = client.post("/api/setup", json={"password": "testpass1234"})
    assert r.status_code == 200, r.text

    # 4. Login + real API access.
    assert client.post("/api/login", json={"password": "testpass1234"}).status_code == 200
    assert client.get("/api/bills").status_code == 200

    # 5. Status reports the active license.
    body = client.get("/api/license/status").json()
    assert body["activated"] is True and body["license"]["no"] == 30


def test_wizard_completes_after_activation(client, real_gate):
    key = mint(no=31)
    assert client.post("/api/license/activate",
                       json={"license_key": key}).status_code == 200
    r = client.post("/api/setup/wizard",
                    json={"password": "testpass1234", "business_type": "retail"})
    assert r.status_code == 200, r.text
    with db.conn() as c:
        n = c.execute("SELECT COUNT(*) AS n FROM price_categories").fetchone()["n"]
    assert n == 3  # retail template
    assert db.get_setting("setup_completed", "") == "true"


def test_status_shows_reason_for_copied_db(client, real_gate):
    """Owner restores a backup made on ANOTHER machine -> status endpoint
    explains machine_mismatch instead of a bare 'not activated'."""
    other_machine_key = mint(sid="9999" + "F" * 12, no=32)
    db.set_setting("license_key", other_machine_key)
    licensing._reset_cache()
    body = client.get("/api/license/status").json()
    assert body["activated"] is False
    assert body["reason"] == licensing.R_MACHINE
    # The locked API still answers with license_required:
    assert client.get("/api/bills").json()["code"] == "license_required"


# ═══════════════════════════════════════════════════════════════════════════
# Owner tool end-to-end (scripts/generate_license.py)
# ═══════════════════════════════════════════════════════════════════════════

def test_owner_generator_script(tmp_path):
    key_file = tmp_path / "owner.pem"
    # 1. --init creates a keypair and prints the public key.
    r = subprocess.run(
        [sys.executable, str(PROJ / "scripts" / "generate_license.py"),
         "--init", "--key-file", str(key_file)],
        capture_output=True, text=True, cwd=str(PROJ), timeout=60,
    )
    assert r.returncode == 0, r.stderr
    assert key_file.exists()
    m = re.search(r"Public key\s+:\s+(\S+)", r.stdout)
    assert m and len(base64.b64decode(m.group(1))) == 32
    # --init refuses to clobber an existing key.
    r2 = subprocess.run(
        [sys.executable, str(PROJ / "scripts" / "generate_license.py"),
         "--init", "--key-file", str(key_file)],
        capture_output=True, text=True, cwd=str(PROJ), timeout=60,
    )
    assert r2.returncode == 1

    # 2. Issue a perpetual license for THIS machine and verify it against
    #    the test public key printed by --init.
    sid = licensing.setup_id()
    r3 = subprocess.run(
        [sys.executable, str(PROJ / "scripts" / "generate_license.py"),
         "--setup-id", sid, "--name", "Test Customer",
         "--key-file", str(key_file)],
        capture_output=True, text=True, cwd=str(PROJ), timeout=60,
    )
    assert r3.returncode == 0, r3.stderr
    assert "License #" in r3.stdout and "perpetual" in r3.stdout
    # Extract the key (strip wrapping whitespace) and verify it.
    key = re.sub(r"\s+", "", r3.stdout[r3.stdout.index("BBL1."):])
    assert key.startswith("BBL1.")

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    pub = Ed25519PublicKey.from_public_bytes(base64.b64decode(m.group(1)))
    import struct as _struct
    blob = base64.urlsafe_b64decode(key[5:] + "=" * (-len(key[5:]) % 4))
    assert len(blob) == 21 + 64
    pub.verify(blob[21:], blob[:21])  # raises if forged

    # 3. Ledger got exactly one row, with the customer name.
    ledger = tmp_path / "licenses_issued.csv"
    assert ledger.exists()
    with open(ledger, newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    assert rows[0]["name"] == "Test Customer"
    assert rows[0]["setup_id"] == sid
