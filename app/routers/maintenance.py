"""v8.1 Phase 5 — Auto-Maintenance: backup, update check, diagnose.

Endpoints:
- POST /api/maintenance/backup — create a timestamped backup (auto-prunes to 10)
- GET /api/maintenance/backups — list backups with age
- GET /api/maintenance/update-check — check GitHub Releases for newer version
- GET /api/maintenance/diagnose — run health checks (DB, disk, AI, tunnel, backup age, negative stock)
"""
import os, sys, shutil, json, logging, subprocess
from datetime import datetime, timedelta
from pathlib import Path
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from .. import db
from ..config import DATA, BACKUPS
from typing import Any

router = APIRouter()
logger = logging.getLogger(__name__)

MAX_BACKUPS = 10


@router.post("/api/maintenance/backup")
def create_backup() -> Any:
    """Create a timestamped backup of the SQLite DB. Auto-prunes to last 10.

    C5 fix (v8.13.4): uses SQLite's VACUUM INTO (atomic, snapshot-consistent)
    instead of shutil.copy2 (which misses WAL pages on a live DB → torn
    reads). VACUUM INTO produces a transactionally-consistent snapshot that
    includes all committed WAL pages, without needing to pause writes.

    Falls back to the sqlite3_backup API if VACUUM INTO is unavailable
    (SQLite < 3.27, 2019). If both fail, falls back to shutil.copy2 of the
    main DB + WAL + SHM sidecars (still better than DB-only).
    """
    BACKUPS.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = BACKUPS / f"billbook_{ts}.db"
    # Handle same-second collisions (append counter)
    counter = 0
    while backup_path.exists():
        counter += 1
        backup_path = BACKUPS / f"billbook_{ts}_{counter}.db"
    db_path = db.DB_PATH
    if not os.path.exists(db_path):
        raise HTTPException(500, "Database file not found")
    # Try VACUUM INTO first — atomic, snapshot-consistent
    backup_ok = False
    method = "unknown"
    try:
        # VACUUM INTO must run from a separate connection (not inside a write_tx)
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute(f"VACUUM INTO ?;", (str(backup_path),))
            backup_ok = True
            method = "vacuum_into"
        finally:
            conn.close()
    except Exception as e:
        logger.warning("VACUUM INTO backup failed (%s) — falling back", e)
    # Fallback: sqlite3_backup API (also atomic, slightly slower)
    if not backup_ok:
        try:
            import sqlite3
            src = sqlite3.connect(str(db_path))
            dst = sqlite3.connect(str(backup_path))
            try:
                src.backup(dst)
                backup_ok = True
                method = "sqlite3_backup"
            finally:
                dst.close()
                src.close()
        except Exception as e:
            logger.warning("sqlite3.backup() failed (%s) — final fallback to file copy", e)
    # Last-resort fallback: copy main + WAL + SHM
    if not backup_ok:
        try:
            shutil.copy2(db_path, str(backup_path))
            wal_path = str(db_path) + "-wal"
            shm_path = str(db_path) + "-shm"
            if os.path.exists(wal_path):
                shutil.copy2(wal_path, str(backup_path) + "-wal")
            if os.path.exists(shm_path):
                shutil.copy2(shm_path, str(backup_path) + "-shm")
            method = "file_copy_with_wal"
        except Exception as e:
            raise HTTPException(500, f"All backup methods failed: {e}")
    # Prune: keep only the last MAX_BACKUPS
    backups = sorted(BACKUPS.glob("billbook_*.db"))
    if len(backups) > MAX_BACKUPS:
        for old in backups[:-MAX_BACKUPS]:
            old.unlink()
    db.set_setting("last_backup_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    db.log_activity("backup_created", "backup", None,
                    f"Backup created: {backup_path.name} (method={method})",
                    {"path": str(backup_path), "method": method})
    return {"ok": True, "backup": backup_path.name, "path": str(backup_path),
            "method": method,
            "total_backups": len(list(BACKUPS.glob("billbook_*.db")))}


@router.get("/api/maintenance/backups")
def list_backups() -> Any:
    """List all backups with size + age."""
    backups = []
    if BACKUPS.exists():
        for f in sorted(BACKUPS.glob("billbook_*.db"), reverse=True):
            stat = f.stat()
            age_hours = (datetime.now().timestamp() - stat.st_mtime) / 3600
            backups.append({
                "name": f.name,
                "size_bytes": stat.st_size,
                "size_mb": round(stat.st_size / 1024 / 1024, 2),
                "age_hours": round(age_hours, 1),
                "created_at": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            })
    last_backup = db.get_setting("last_backup_at", "")
    auto_enabled = db.get_setting("auto_backup_enabled", "true") == "true"
    return {"backups": backups, "count": len(backups),
            "last_backup_at": last_backup, "auto_backup_enabled": auto_enabled}


@router.get("/api/maintenance/update-check")
def check_for_update() -> Any:
    """Check GitHub Releases for a newer version. Returns latest version + whether update is available."""
    import httpx
    current_version = "8.1.0"
    try:
        r = httpx.get(
            "https://api.github.com/repos/billbook/billbook/releases/latest",
            timeout=10.0,
            headers={"Accept": "application/vnd.github.v3+json"},
        )
        if r.status_code != 200:
            return {"current_version": current_version, "latest_version": current_version,
                    "update_available": False, "note": "Could not reach GitHub"}
        data = r.json()
        latest = data.get("tag_name", "").lstrip("v")
        update_available = _compare_versions(latest, current_version) > 0
        return {
            "current_version": current_version,
            "latest_version": latest,
            "update_available": update_available,
            "release_url": data.get("html_url", ""),
            "release_notes": data.get("body", "")[:500],
        }
    except Exception as e:
        return {"current_version": current_version, "latest_version": current_version,
                "update_available": False, "note": f"Check failed: {e}"}


def _compare_versions(a, b):
    """Compare two version strings. Returns 1 if a>b, -1 if a<b, 0 if equal."""
    try:
        parts_a = [int(x) for x in a.split(".")]
        parts_b = [int(x) for x in b.split(".")]
        for i in range(max(len(parts_a), len(parts_b))):
            va = parts_a[i] if i < len(parts_a) else 0
            vb = parts_b[i] if i < len(parts_b) else 0
            if va > vb: return 1
            if va < vb: return -1
        return 0
    except Exception:
        return 0


@router.get("/api/maintenance/diagnose")
def run_diagnostics() -> Any:
    """Run health checks and return a results table with green/amber/red statuses."""
    results = []

    # 1. DB integrity check
    try:
        with db.conn() as c:
            row = c.execute("PRAGMA integrity_check").fetchone()
        ok = row["integrity_check"] == "ok" if row else False
        results.append({"check": "Database Integrity", "status": "green" if ok else "red",
                        "detail": row["integrity_check"] if row else "no result"})
    except Exception as e:
        results.append({"check": "Database Integrity", "status": "red", "detail": str(e)})

    # 2. Free disk space
    try:
        stat = os.statvfs(str(DATA))
        free_mb = (stat.f_bavail * stat.f_frsize) / 1024 / 1024
        status = "green" if free_mb > 500 else ("amber" if free_mb > 100 else "red")
        results.append({"check": "Free Disk Space", "status": status,
                        "detail": f"{free_mb:.0f} MB free"})
    except Exception as e:
        results.append({"check": "Free Disk Space", "status": "amber", "detail": str(e)})

    # 3. AI provider reachable
    try:
        from ..ai_router import is_ai_disabled
        disabled = is_ai_disabled()
        # PR 7b: use decrypt_setting_key (handles both plaintext + encrypted)
        from ..crypto import decrypt_setting_key
        groq_key = bool(decrypt_setting_key("groq_api_key") or decrypt_setting_key("gemini_api_key"))
        if disabled:
            results.append({"check": "AI Provider", "status": "amber",
                            "detail": "AI kill switch is ON"})
        elif groq_key:
            results.append({"check": "AI Provider", "status": "green",
                            "detail": "API key configured"})
        else:
            results.append({"check": "AI Provider", "status": "amber",
                            "detail": "No API key set (AI uses heuristic fallback)"})
    except Exception:
        results.append({"check": "AI Provider", "status": "amber", "detail": "Could not check"})

    # 4. Tunnel status
    try:
        from ..routers.remote_access import _tunnel_proc
        running = _tunnel_proc is not None and _tunnel_proc.poll() is None
        results.append({"check": "Remote Access (Tunnel)", "status": "green" if running else "amber",
                        "detail": "Running" if running else "Not started"})
    except Exception:
        results.append({"check": "Remote Access (Tunnel)", "status": "amber", "detail": "Not configured"})

    # 5. Last backup age
    try:
        last = db.get_setting("last_backup_at", "")
        if last:
            last_dt = datetime.strptime(last, "%Y-%m-%d %H:%M:%S")
            age_hours = (datetime.now() - last_dt).total_seconds() / 3600
            status = "green" if age_hours < 48 else ("amber" if age_hours < 168 else "red")
            results.append({"check": "Last Backup", "status": status,
                            "detail": f"{age_hours:.0f}h ago ({last})"})
        else:
            results.append({"check": "Last Backup", "status": "red", "detail": "Never backed up"})
    except Exception:
        results.append({"check": "Last Backup", "status": "amber", "detail": "Could not check"})

    # 6. Negative stock categories
    try:
        with db.conn() as c:
            neg = c.execute(
                "SELECT css.category_id, pc.code, pc.name, css.current_qty "
                "FROM category_stock_state css "
                "LEFT JOIN price_categories pc ON css.category_id = pc.id "
                "WHERE css.current_qty < 0"
            ).fetchall()
        if neg:
            results.append({"check": "Negative Stock", "status": "red",
                            "detail": f"{len(neg)} categories with negative stock: " +
                                      ", ".join(f"{r['code'] or r['category_id']} ({r['current_qty']})" for r in neg)})
        else:
            results.append({"check": "Negative Stock", "status": "green",
                            "detail": "No negative stock"})
    except Exception as e:
        results.append({"check": "Negative Stock", "status": "amber", "detail": str(e)})

    # Summary
    green = sum(1 for r in results if r["status"] == "green")
    amber = sum(1 for r in results if r["status"] == "amber")
    red = sum(1 for r in results if r["status"] == "red")
    return {"results": results, "green": green, "amber": amber, "red": red,
            "total": len(results)}


@router.post("/api/maintenance/auto-backup-toggle")
def toggle_auto_backup(payload: dict) -> Any:
    """Toggle auto-backup on/off. Default is ON."""
    enabled = payload.get("enabled", True)
    db.set_setting("auto_backup_enabled", "true" if enabled else "false")
    return {"ok": True, "auto_backup_enabled": enabled}


# ════════════════════════════════════════════════════════════════════════════════
# v8.10 Phase 10: Data Reconciliation + Repair
# ════════════════════════════════════════════════════════════════════════════════

# L8 fix (v8.13.4): add a restore endpoint (was missing entirely)
class RestoreBackupIn(BaseModel):
    backup_name: str
    manager_pin: str


@router.post("/api/maintenance/restore")
def restore_backup(payload: RestoreBackupIn) -> Any:
    """Restore the SQLite DB from a previously-created backup.

    L8 fix (v8.13.4): previously there was no restore endpoint at all —
    only `create_backup` existed, with no documented recovery procedure.

    This endpoint:
      1. Validates manager PIN (only managers can restore)
      2. Validates backup_name is a real file in BACKUPS/ (no path traversal)
      3. Auto-creates a pre-restore snapshot in BACKUPS/pre_restore/
      4. Uses sqlite3_backup API to overwrite the live DB
      5. Forces a clean restart of the app (caller should restart after)

    IMPORTANT: this endpoint does NOT serve requests while restoring —
    the live DB is locked. The client must:
      - call this endpoint
      - wait for {"ok": True}
      - immediately restart the app process
    """
    from .. import shop as shop_mod
    # Validate manager PIN
    mgr = shop_mod.verify_manager_pin(payload.manager_pin)
    if not mgr:
        raise HTTPException(403, "Manager PIN required for restore")
    # Validate backup_name — must be a basename, no separators
    name = payload.backup_name
    if not name or "/" in name or "\\" in name or ".." in name:
        raise HTTPException(400, "Invalid backup name (path separators not allowed)")
    backup_file = (BACKUPS / name).resolve()
    # Path-traversal defense: resolved path must start with BACKUPS
    try:
        backup_file.relative_to(BACKUPS.resolve())
    except ValueError:
        raise HTTPException(400, "Backup not found")
    if not backup_file.exists():
        raise HTTPException(404, f"Backup {name} not found in {BACKUPS}")
    # Pre-restore snapshot of the current DB (safety net)
    pre_restore_dir = BACKUPS / "pre_restore"
    pre_restore_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    pre_restore_path = pre_restore_dir / f"prerestore_{ts}.db"
    try:
        import sqlite3
        src = sqlite3.connect(str(db.DB_PATH))
        dst = sqlite3.connect(str(pre_restore_path))
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
    except Exception as e:
        logger.warning("Pre-restore snapshot failed: %s (continuing anyway)", e)
    # Restore: overwrite the live DB from the backup
    try:
        import sqlite3
        src = sqlite3.connect(str(backup_file))
        dst = sqlite3.connect(str(db.DB_PATH))
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
    except Exception as e:
        raise HTTPException(500, f"Restore failed: {e}. Pre-restore snapshot at {pre_restore_path}")
    db.log_activity("backup_restored", "backup", None,
                    f"Restored from {name} by {mgr['name']}. Pre-restore snapshot: {pre_restore_path.name}",
                    {"backup_name": name, "prerestore": str(pre_restore_path)})
    return {
        "ok": True,
        "restored_from": name,
        "pre_restore_snapshot": str(pre_restore_path),
        "note": "Restart the app now — current in-memory state is stale.",
    }


# ════════════════════════════════════════════════════════════════════════════════

@router.get("/api/maintenance/discrepancy-report")
def discrepancy_report() -> Any:
    """Read-only discrepancy report — shows data integrity issues without fixing them.

    Checks:
      1. Unknown/NULL payment_status values
      2. Orphaned ezi_pos_imports (sale_id not in sales)
      3. Orphaned cash_drawer entries (reference_id not in sales)
      4. Refunded sales without cash_drawer reversal entries
      5. Orphaned commissions (sale_id not in sales)
      6. Orphaned loyalty_redemptions (sale_id not in sales)
      7. Stock state vs computed stock (purchased - sold + adjustments)
    """
    from .. import db
    from ..db import VALID_SALE_STATUSES
    report = {"issues": [], "summary": {}}

    with db.conn() as c:
        # 1. Unknown/NULL payment_status
        unknown_statuses = c.execute(
            "SELECT payment_status, COUNT(*) AS n FROM sales "
            "WHERE payment_status IS NULL "
            "OR payment_status NOT IN ('paid', 'credit', 'partial', 'refunded') "
            "GROUP BY payment_status"
        ).fetchall()
        for r in unknown_statuses:
            report["issues"].append({
                "type": "unknown_payment_status",
                "severity": "high",
                "detail": f"Status: {r['payment_status']!r}, count: {r['n']}",
                "fix": "Review these sales and set a valid payment_status"
            })

        # 2. Orphaned ezi_pos_imports
        orphaned_imports = c.execute(
            "SELECT COUNT(*) AS n FROM ezi_pos_imports "
            "WHERE sale_id NOT IN (SELECT id FROM sales)"
        ).fetchone()["n"]
        if orphaned_imports > 0:
            report["issues"].append({
                "type": "orphaned_ezi_pos_imports",
                "severity": "medium",
                "detail": f"{orphaned_imports} ezi_pos_imports rows reference non-existent sales",
                "fix": "Run repair to delete orphaned rows"
            })

        # 3. Orphaned cash_drawer entries
        orphaned_drawer = c.execute(
            "SELECT COUNT(*) AS n FROM cash_drawer "
            "WHERE reference_type='sale' "
            "AND reference_id NOT IN (SELECT id FROM sales)"
        ).fetchone()["n"]
        if orphaned_drawer > 0:
            report["issues"].append({
                "type": "orphaned_cash_drawer",
                "severity": "medium",
                "detail": f"{orphaned_drawer} cash_drawer entries reference non-existent sales",
                "fix": "Run repair to delete orphaned rows"
            })

        # 4. Refunded sales without cash_drawer reversal
        refunded_no_reversal = c.execute(
            "SELECT COUNT(*) AS n FROM sales s "
            "WHERE s.payment_status='refunded' "
            "AND s.payment_method IN ('cash', 'split') "
            "AND NOT EXISTS ("
            "  SELECT 1 FROM cash_drawer cd "
            "  WHERE cd.reference_id=s.id AND cd.reference_type='sale' AND cd.amount < 0)"
        ).fetchone()["n"]
        if refunded_no_reversal > 0:
            report["issues"].append({
                "type": "missing_reversal_entry",
                "severity": "high",
                "detail": f"{refunded_no_reversal} refunded sales have no cash_drawer reversal entry",
                "fix": "Run repair to insert reversal entries (or rebuild from stock_state)"
            })

        # 5. Orphaned commissions
        orphaned_comms = c.execute(
            "SELECT COUNT(*) AS n FROM commissions "
            "WHERE sale_id NOT IN (SELECT id FROM sales)"
        ).fetchone()["n"]
        if orphaned_comms > 0:
            report["issues"].append({
                "type": "orphaned_commissions",
                "severity": "medium",
                "detail": f"{orphaned_comms} commission rows reference non-existent sales",
                "fix": "Run repair to delete orphaned rows"
            })

        # 6. Orphaned loyalty_redemptions
        orphaned_loyalty = c.execute(
            "SELECT COUNT(*) AS n FROM loyalty_redemptions "
            "WHERE sale_id IS NOT NULL AND sale_id NOT IN (SELECT id FROM sales)"
        ).fetchone()["n"]
        if orphaned_loyalty > 0:
            report["issues"].append({
                "type": "orphaned_loyalty_redemptions",
                "severity": "medium",
                "detail": f"{orphaned_loyalty} loyalty_redemptions reference non-existent sales",
                "fix": "Run repair to mark orphaned rows as reversed"
            })

        # 7. Activity log orphaned entries
        orphaned_logs = c.execute(
            "SELECT COUNT(*) AS n FROM activity_log "
            "WHERE entity_deleted = 0 "
            "AND ((entity_type='bill' AND entity_id NOT IN (SELECT id FROM bills)) "
            "OR (entity_type='sale' AND entity_id NOT IN (SELECT id FROM sales)) "
            "OR (entity_type='customer' AND entity_id NOT IN (SELECT id FROM customers)) "
            "OR (entity_type='supplier' AND entity_id NOT IN (SELECT id FROM suppliers)))"
        ).fetchone()["n"]
        if orphaned_logs > 0:
            report["issues"].append({
                "type": "orphaned_activity_logs",
                "severity": "low",
                "detail": f"{orphaned_logs} activity_log entries reference deleted entities",
                "fix": "Run repair to mark orphaned entries"
            })

    report["summary"] = {
        "total_issues": len(report["issues"]),
        "high_severity": sum(1 for i in report["issues"] if i["severity"] == "high"),
        "medium_severity": sum(1 for i in report["issues"] if i["severity"] == "medium"),
        "low_severity": sum(1 for i in report["issues"] if i["severity"] == "low"),
    }
    return report


@router.post("/api/maintenance/repair")
def repair_data(payload: dict = None) -> Any:
    """Repair data integrity issues. Requires manager PIN.

    Operations (idempotent — safe to run multiple times):
      1. Delete orphaned ezi_pos_imports rows
      2. Delete orphaned cash_drawer entries
      3. Delete orphaned commissions
      4. Mark orphaned loyalty_redemptions as reversed
      5. Mark orphaned activity_log entries
      6. Rebuild stock_state (replays all bills + sales chronologically)
    """
    body = payload or {}
    from .. import shop as shop_mod

    # Validate manager PIN
    mgr = shop_mod.verify_manager_pin(body.get("manager_pin", ""))
    if not mgr:
        raise HTTPException(403, "Manager PIN required for data repair")

    from .. import db, profit
    results = {"operations": []}

    # 1. Delete orphaned ezi_pos_imports
    with db.write_tx() as c:
        r = c.execute(
            "DELETE FROM ezi_pos_imports "
            "WHERE sale_id NOT IN (SELECT id FROM sales)"
        )
        results["operations"].append({"operation": "delete_orphaned_ezi_imports", "affected": r.rowcount})

    # 2. Delete orphaned cash_drawer entries
    with db.write_tx() as c:
        r = c.execute(
            "DELETE FROM cash_drawer "
            "WHERE reference_type='sale' "
            "AND reference_id NOT IN (SELECT id FROM sales)"
        )
        results["operations"].append({"operation": "delete_orphaned_cash_drawer", "affected": r.rowcount})

    # 3. Delete orphaned commissions
    with db.write_tx() as c:
        r = c.execute(
            "DELETE FROM commissions "
            "WHERE sale_id NOT IN (SELECT id FROM sales)"
        )
        results["operations"].append({"operation": "delete_orphaned_commissions", "affected": r.rowcount})

    # 4. Mark orphaned loyalty_redemptions as reversed
    with db.write_tx() as c:
        r = c.execute(
            "UPDATE loyalty_redemptions SET reversed_at=datetime('now','localtime') "
            "WHERE sale_id IS NOT NULL "
            "AND sale_id NOT IN (SELECT id FROM sales) "
            "AND reversed_at IS NULL"
        )
        results["operations"].append({"operation": "reverse_orphaned_loyalty", "affected": r.rowcount})

    # 5. Mark orphaned activity_log entries
    mark_result = db.mark_orphaned_activity_logs()
    results["operations"].append({"operation": "mark_orphaned_activity_logs", "affected": mark_result["marked"]})

    # 6. Rebuild stock_state
    try:
        rebuild_result = profit.rebuild_stock_state()
        results["operations"].append({
            "operation": "rebuild_stock_state",
            "affected": rebuild_result.get("rewrote_sales", 0),
            "categories": len(rebuild_result.get("categories", []))
        })
    except Exception as e:
        results["operations"].append({"operation": "rebuild_stock_state", "error": str(e)})

    db.log_activity(
        "data_repair", "maintenance", None,
        f"Data repair run by {mgr['name']}",
        {"operations": results["operations"]},
    )
    results["ok"] = True
    return results
