"""v8.19: License vs. backups — "license never travels in backups".

Scenario matrix (setup: PC-A and PC-B each have their OWN license):
  1. create_backup() scrubs license_* rows -> backups are pure data
     (safe to copy/move/share; no license leaks inside them).
  2. Restore on the SAME licensed machine -> license preserved, app stays
     unlocked, no re-activation needed (the everyday recovery flow).
  3. Restore of a FOREIGN backup on a licensed machine (PC-B restoring
     PC-A's data, incl. legacy backups that still carry PC-A's license)
     -> PC-B KEEPS its own license and stays unlocked.
  4. Restore on an UNLICENSED machine -> no license is smuggled in:
     scrubbed backup leaves it locked (missing); a legacy backup's foreign
     license locks with machine_mismatch — exactly like a copied data dir.
"""
import sqlite3
import time
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import conftest
from app import db, licensing
from app.routers.maintenance import RestoreBackupIn, create_backup, restore_backup

# ─── Test keypair (independent of the production key embedded in the app) ───
_test_key = Ed25519PrivateKey.generate()
TEST_PRIV_PEM = _test_key.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
)
TEST_PUB_B64 = __import__("base64").b64encode(
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


@pytest.fixture()
def owner_pin(tmp_db_path):
    """Set the owner password so manager-PIN checks pass via the password
    fallback (the same path a real single-owner install uses)."""
    from app.security import hash_password
    db.set_setting("password_hash", hash_password("owner-pw-123"))
    return "owner-pw-123"


def mint(sid=None, no=1, exp=None):
    return licensing.make_license_key(
        TEST_PRIV_PEM, sid or licensing.setup_id(), no, int(time.time()), exp,
    )


def _license_rows_in(db_file) -> dict:
    con = sqlite3.connect(str(db_file))
    try:
        rows = con.execute(
            "SELECT key, value FROM settings WHERE key LIKE 'license_%'").fetchall()
        return {k: v for k, v in rows}
    finally:
        con.close()


def _plant_in_backup(backup_file: Path, rows: dict):
    """Simulate a LEGACY backup (made by a pre-scrub app version) that still
    carries license rows from the machine that created it."""
    con = sqlite3.connect(str(backup_file))
    try:
        for k, v in rows.items():
            con.execute("INSERT OR REPLACE INTO settings(key, value) VALUES(?,?)",
                        (k, v))
        con.commit()
    finally:
        con.close()


FOREIGN_SID = "9999" + "F" * 12   # a different machine's Setup ID


# ═══════════════════════════════════════════════════════════════════════════
# 1. Backups are license-free
# ═══════════════════════════════════════════════════════════════════════════

def test_backup_scrubs_license_rows(tmp_db_path, real_gate):
    assert licensing.activate(mint(no=10))[0] is True
    r = create_backup()
    assert r["ok"] is True
    assert r["license_rows_scrubbed"] >= 1
    backup = Path(r["path"])
    assert backup.exists()
    assert _license_rows_in(backup) == {}, "backup must not carry license rows"


def test_scrub_helper_removes_planted_rows(tmp_db_path, real_gate):
    r = create_backup()                      # fresh (already clean) backup
    backup = Path(r["path"])
    _plant_in_backup(backup, {"license_key": mint(sid=FOREIGN_SID, no=11),
                              "license_payload": "{}"})
    assert _license_rows_in(backup) != {}
    removed = licensing.scrub_license_from_db_file(backup)
    assert removed == 2
    assert _license_rows_in(backup) == {}


def test_scrub_helper_tolerates_settingsless_file(tmp_path):
    """An ancient DB file without a settings table is left untouched."""
    f = tmp_path / "old.db"
    con = sqlite3.connect(str(f))
    con.execute("CREATE TABLE t(x)")
    con.commit()
    con.close()
    assert licensing.scrub_license_from_db_file(f) == 0
    assert f.exists()


# ═══════════════════════════════════════════════════════════════════════════
# 2/3. Restore keeps THIS machine's license (same machine + foreign backup)
# ═══════════════════════════════════════════════════════════════════════════

def test_restore_same_machine_keeps_license(tmp_db_path, real_gate, owner_pin):
    my_key = mint(no=20)
    assert licensing.activate(my_key)[0] is True
    r = create_backup()
    assert _license_rows_in(r["path"]) == {}
    res = restore_backup(RestoreBackupIn(backup_name=r["backup"],
                                         manager_pin=owner_pin))
    assert res["ok"] is True and res["license_preserved"] is True
    # The machine's own license survived the restore — still active, no
    # re-activation needed:
    assert db.get_setting("license_key", "") == my_key
    licensing._reset_cache()
    assert licensing.is_activated() is True


def test_restore_foreign_backup_keeps_own_license(tmp_db_path, real_gate, owner_pin):
    """THE user scenario: PC-B (own license) restores PC-A's backup."""
    my_key = mint(no=30)                     # PC-B's own license
    assert licensing.activate(my_key)[0] is True
    r = create_backup()
    backup = Path(r["path"])
    # PC-A's backup: same data, but made on another machine — plant PC-A's
    # license rows into it (what a legacy pre-scrub backup would contain).
    _plant_in_backup(backup, {"license_key": mint(sid=FOREIGN_SID, no=31),
                              "license_payload": '{"no": 31}',
                              "license_activated_at": "2026-01-01T00:00:00"})
    res = restore_backup(RestoreBackupIn(backup_name=r["backup"],
                                         manager_pin=owner_pin))
    assert res["ok"] is True and res["license_preserved"] is True
    # PC-B keeps ITS OWN license — not PC-A's:
    assert db.get_setting("license_key", "") == my_key
    assert mint(sid=FOREIGN_SID, no=31) != db.get_setting("license_key", "")
    licensing._reset_cache()
    assert licensing.is_activated() is True


def test_restore_no_license_rows_backup_on_licensed_machine(
        tmp_db_path, real_gate, owner_pin):
    """Backup made by THIS version (no license rows at all) restores cleanly
    on a licensed machine."""
    my_key = mint(no=40)
    assert licensing.activate(my_key)[0] is True
    r = create_backup()
    # sanity: scrubbed backup has no license rows to offer
    assert _license_rows_in(r["path"]) == {}
    res = restore_backup(RestoreBackupIn(backup_name=r["backup"],
                                         manager_pin=owner_pin))
    assert res["ok"] is True
    assert db.get_setting("license_key", "") == my_key
    licensing._reset_cache()
    assert licensing.is_activated() is True


# ═══════════════════════════════════════════════════════════════════════════
# 4. Restore never smuggles a license onto an unlicensed machine
# ═══════════════════════════════════════════════════════════════════════════

def test_restore_scrubbed_backup_on_unlicensed_machine_stays_locked(
        tmp_db_path, real_gate, owner_pin):
    # Machine is NOT licensed; make a data-only backup, then "reset" the app
    # (fresh DB via the same tmp dir) and restore.
    db.set_setting("some_data_setting", "hello")
    r = create_backup()
    # wipe live license state + the data setting to simulate the reset
    with db.conn() as c:
        c.execute("DELETE FROM settings WHERE key LIKE 'license_%'")
        c.execute("DELETE FROM settings WHERE key='some_data_setting'")
    licensing._reset_cache()
    assert licensing.is_activated() is False
    res = restore_backup(RestoreBackupIn(backup_name=r["backup"],
                                         manager_pin=owner_pin))
    assert res["ok"] is True and res["license_preserved"] is False
    # Data came back...
    assert db.get_setting("some_data_setting", "") == "hello"
    # ...but NO license did:
    assert db.get_setting("license_key", "") == ""
    licensing._reset_cache()
    state = licensing.license_state()
    assert state["activated"] is False and state["reason"] == licensing.R_MISSING


def test_restore_legacy_foreign_backup_on_unlicensed_machine_locks(
        tmp_db_path, real_gate, owner_pin):
    """Unlicensed machine restores a legacy backup that still carries PC-A's
    license -> v8.19.1 delete-first reapply WIPES the foreign rows (they were
    never this machine's): it stays unlicensed with the plain 'missing'
    reason — the normal activation screen, not a confusing machine_mismatch
    from a license that was never entered on this machine."""
    r = create_backup()
    backup = Path(r["path"])
    _plant_in_backup(backup, {"license_key": mint(sid=FOREIGN_SID, no=50)})
    res = restore_backup(RestoreBackupIn(backup_name=r["backup"],
                                         manager_pin=owner_pin))
    assert res["ok"] is True and res["license_preserved"] is False
    # v8.19.1: the foreign license is NOT inherited — the DB stays license-free
    assert db.get_setting("license_key", "") == ""
    licensing._reset_cache()
    state = licensing.license_state()
    assert state["activated"] is False
    assert state["reason"] == licensing.R_MISSING


def test_restore_rejects_wrong_pin(tmp_db_path, real_gate, owner_pin):
    r = create_backup()
    with pytest.raises(Exception):
        restore_backup(RestoreBackupIn(backup_name=r["backup"],
                                       manager_pin="wrong-pin"))


# ═══════════════════════════════════════════════════════════════════════════
# 5. v8.19.1: the UI restore path (/api/backup/restore in bills.py — the
#    Settings → Backups → Restore flow) follows the SAME policy.
#    TAU-41 only fixed /api/maintenance/restore; the UI endpoint silently
#    wiped the local license whenever a foreign backup was restored.
# ═══════════════════════════════════════════════════════════════════════════

from app.routers import bills as bills_router


def _make_backup_dir(name: str, source_db: Path, plant: dict | None = None,
                     scrub: bool = False) -> str:
    """Create BACKUPS/<name>/billbook.db from source_db (optionally
    planting foreign license rows = legacy backup, or scrubbing = current
    version's backup). Returns the backup dir name.

    Uses the sqlite3 backup API (NOT shutil.copy2) — the live DB runs in
    WAL mode and copy2 would miss everything still sitting in the -wal file.
    """
    backup_dir = Path(bills_router.BACKUPS) / name
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / "billbook.db"
    src = sqlite3.connect(str(source_db))
    dst = sqlite3.connect(str(target))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    if plant or scrub:
        con = sqlite3.connect(str(target))
        try:
            if scrub:
                con.execute("DELETE FROM settings WHERE key LIKE 'license_%'")
            for k, v in (plant or {}).items():
                con.execute(
                    "INSERT OR REPLACE INTO settings(key, value) VALUES(?,?)",
                    (k, v))
            con.commit()
        finally:
            con.close()
    return name


def test_ui_restore_foreign_backup_keeps_own_license(
        tmp_db_path, real_gate, owner_pin):
    """THE reported bug: licensed PC-B restores a backup that came from a
    different PC through Settings → Backups → Restore (/api/backup/restore)
    -> PC-B keeps its own license (previously: wiped, forced re-activation)."""
    my_key = mint(no=60)
    assert licensing.activate(my_key)[0] is True

    # Build PC-A's backup FIRST (from the current live DB), carrying a
    # FOREIGN license + PC-A's data marker (legacy pre-scrub backup).
    name = _make_backup_dir("foreign_pc_a", Path(db.DB_PATH), plant={
        "license_key": mint(sid=FOREIGN_SID, no=61),
        "license_payload": '{"no": 61}',
        "pc_a_marker": "yes",
    })
    # PC-B-only marker, set AFTER the backup was taken — proves the DB was
    # really replaced by the restore.
    db.set_setting("pc_b_marker", "here")

    res = bills_router.restore_backup(
        {"name": name, "manager_pin": owner_pin})
    assert res["ok"] is True
    assert res["license_preserved"] is True
    assert res["license_settings_reapplied"] >= 1
    # PC-A's data arrived (the DB really was replaced)...
    assert db.get_setting("pc_a_marker", "") == "yes"
    assert db.get_setting("pc_b_marker", "") == ""
    # ...but PC-B's OWN license survived — no re-activation needed:
    assert db.get_setting("license_key", "") == my_key
    licensing._reset_cache()
    assert licensing.is_activated() is True


def test_ui_restore_scrubbed_backup_keeps_own_license(
        tmp_db_path, real_gate, owner_pin):
    """Same flow with a CURRENT-version backup (license-free) — the everyday
    'restore my own backup' recovery keeps the machine licensed."""
    my_key = mint(no=62)
    assert licensing.activate(my_key)[0] is True
    name = _make_backup_dir("own_data", Path(db.DB_PATH), scrub=True)
    res = bills_router.restore_backup(
        {"name": name, "manager_pin": owner_pin})
    assert res["ok"] is True and res["license_preserved"] is True
    assert db.get_setting("license_key", "") == my_key
    licensing._reset_cache()
    assert licensing.is_activated() is True


def test_ui_restore_wipes_foreign_license_on_unlicensed_machine(
        tmp_db_path, real_gate, owner_pin):
    """Unlicensed machine + legacy foreign backup via the UI path: the
    foreign license is wiped (not inherited) — machine stays unlicensed."""
    assert licensing.is_activated() is False
    name = _make_backup_dir("legacy_foreign", Path(db.DB_PATH), plant={
        "license_key": mint(sid=FOREIGN_SID, no=63),
    })
    res = bills_router.restore_backup(
        {"name": name, "manager_pin": owner_pin})
    assert res["ok"] is True
    assert res["license_preserved"] is False
    assert db.get_setting("license_key", "") == ""
    licensing._reset_cache()
    assert licensing.is_activated() is False


def test_ui_restore_rejects_bad_name(tmp_db_path, real_gate, owner_pin):
    with pytest.raises(Exception):
        bills_router.restore_backup(
            {"name": "../evil", "manager_pin": owner_pin})


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
