"""v8.2.3 — Third-party POS backup import router."""
import os, tempfile, shutil, secrets
from typing import Any
from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel
from .. import db
from ..pos_import_sync import (
    import_pos_backup, get_pos_import_summary, get_pos_import_history,
    HAS_DBFREAD, delete_pos_import, delete_pos_import_by_activity_log_id,
)

router = APIRouter()

# v8.13.1: ZIP magic bytes — must start with "PK" (0x50 0x4B).
# Validates the file is actually a ZIP, not just an attacker-renamed .zip file.
ZIP_MAGIC = b"PK\x03\x04"
ZIP_MAGIC_EMPTY = b"PK\x05\x06"  # empty archive
ALLOWED_ZIP_MAGIC = (ZIP_MAGIC, ZIP_MAGIC_EMPTY)


def _save_upload_safely(data: bytes, original_filename: str) -> str:
    """Save uploaded bytes to a temp file with a server-generated name.

    SECURITY (v8.13.1): We NEVER use the user-supplied filename directly —
    if the attacker sends filename="/etc/cron.d/evil.zip" or
    "../../home/z/.bashrc.zip", os.path.join(tempdir, filename) would
    discard the tempdir prefix (Python semantics) and write to the
    attacker-chosen absolute path → RCE via cron/.bashrc overwrite.

    The fix: generate a random filename server-side, validate the magic
    bytes are actually ZIP, and only use the basename of the original
    filename for the description (never for the path).
    """
    # Validate magic bytes before writing to disk
    if not data.startswith(ZIP_MAGIC):
        raise HTTPException(400, "Invalid file: not a valid ZIP archive (magic bytes mismatch)")
    # Generate a random server-side filename — never trust user input for the path
    safe_name = f"pos_upload_{secrets.token_hex(8)}.zip"
    temp_dir = tempfile.mkdtemp(prefix="pos_upload_")
    temp_path = os.path.join(temp_dir, safe_name)
    # Sanity: verify the resolved path is still inside temp_dir (defense in depth)
    if not os.path.abspath(temp_path).startswith(os.path.abspath(temp_dir) + os.sep):
        # This should be impossible given safe_name has no path separators, but check anyway
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise HTTPException(400, "Invalid filename — path traversal detected")
    with open(temp_path, 'wb') as f:
        f.write(data)
    return temp_path


@router.post("/api/pos-import/upload")
async def upload_pos_backup(file: UploadFile = File(...)):
    """Upload and import a third-party POS backup zip file.

    The zip should contain DBF files (ACCTRANS.DBF, DIARY.DBF, etc.) from
    the Ezi POS system. Deduplication is automatic via UNQCODE — re-importing
    the same backup or importing a newer one (which contains all old + new
    records) never duplicates data.

    Accepts: multipart/form-data with a 'file' field containing the BU*.zip

    SECURITY (v8.13.1): Validates magic bytes + uses server-generated filename
    to prevent path-traversal RCE.
    """
    if not HAS_DBFREAD:
        raise HTTPException(500, "dbfread library not installed. Run: pip install dbfread")
    if not file.filename or not file.filename.lower().endswith('.zip'):
        raise HTTPException(400, "File must be a .zip archive")
    # Read with size limit enforced BEFORE the full read
    # (the httpx/starlette UploadFile supports .read(size) for streaming)
    data = await file.read(100 * 1024 * 1024 + 1)  # 100MB + 1 byte to detect overflow
    if len(data) > 100 * 1024 * 1024:
        raise HTTPException(413, "File too large (max 100MB)")
    # Save safely (validates magic bytes + uses server-generated path)
    temp_path = _save_upload_safely(data, file.filename)
    try:
        result = import_pos_backup(temp_path)
        return result
    except FileNotFoundError as e:
        raise HTTPException(400, f"Invalid backup file: {e}")
    except Exception as e:
        raise HTTPException(500, f"Import failed: {str(e)}")
    finally:
        shutil.rmtree(os.path.dirname(temp_path), ignore_errors=True)


# Alias for the frontend that posts to /upload-zip
@router.post("/api/pos-import/upload-zip")
async def upload_zip_alias(file: UploadFile = File(...)):
    """Alias — the frontend sometimes calls /upload-zip for Ezi POS zips too."""
    return await upload_pos_backup(file)


@router.get("/api/pos-import/summary")
def pos_import_summary() -> Any:
    """Get a summary of all POS imports."""
    return get_pos_import_summary()


@router.get("/api/pos-import/history")
def pos_import_history(limit: int = 20) -> Any:
    """Get recent imported transactions."""
    items = get_pos_import_history(limit)
    return {"imports": items, "count": len(items)}


@router.get("/api/pos-import/status")
def pos_import_status() -> Any:
    """Check if the POS import feature is available."""
    return {
        "available": HAS_DBFREAD,
        "dbfread_installed": HAS_DBFREAD,
        "supported_formats": ["ezi_pos_dbf"],
    }


# v8.5: Delete by import_run_id (preferred) or by activity_log_id (legacy)
@router.delete("/api/pos-import/by-id/{import_run_id}")
def delete_pos_import_by_run(import_run_id: int) -> Any:
    """v8.5: Delete an import run by its pos_imports.id."""
    result = delete_pos_import(import_run_id)
    if not result.get("ok"):
        raise HTTPException(404, result.get("error", "Import not found"))
    return result


@router.delete("/api/pos-import/{activity_log_id}")
def delete_pos_import_route(activity_log_id: int) -> Any:
    """Legacy entry: accepts activity_log.id, resolves to import_run_id."""
    result = delete_pos_import_by_activity_log_id(activity_log_id)
    if not result.get("ok"):
        raise HTTPException(404, result.get("error", "Import not found"))
    return result


@router.get("/api/pos-import/run/{import_run_id}")
def get_pos_import_run(import_run_id: int, limit: int = 1000) -> Any:
    """v8.5: Get details of a specific import run, including the list of
    imported invoices (for drill-down on the frontend).

    `limit` caps the number of sales returned (default 1000) to keep the
    payload reasonable. Use limit=0 for no cap.
    """
    with db.conn() as c:
        run = c.execute(
            "SELECT * FROM pos_imports WHERE id=?", (import_run_id,)
        ).fetchone()
        if not run:
            raise HTTPException(404, "Import run not found")
        # Get all sales linked to this run
        sql = (
            "SELECT s.id, s.invoice_no, s.customer_name, s.customer_phone, "
            "s.total, s.payment_method, s.payment_status, s.created_at "
            "FROM ezi_pos_imports ezi "
            "JOIN sales s ON ezi.sale_id = s.id "
            "WHERE ezi.import_run_id=? "
            "ORDER BY s.created_at DESC"
        )
        params = (import_run_id,)
        if limit and limit > 0:
            sql += " LIMIT ?"
            params = (import_run_id, limit)
        sales = c.execute(sql, params).fetchall()
    return {
        "run": dict(run),
        "sales": [dict(s) for s in sales],
        "sale_count": len(sales),
        "capped": bool(limit and limit > 0 and len(sales) >= limit),
    }



# ════════════════════════════════════════════════════════════════════════════════
# v8.11 Phase 4/6: POS Import Sync — Deleted + Modified Sale Detection
# ════════════════════════════════════════════════════════════════════════════════

@router.get("/api/pos-import/sync-status")
def pos_import_sync_status() -> Any:
    """Check if POS import sync features are enabled + show feature flags."""
    from .. import db
    return {
        "sync_deletions_enabled": db.get_setting("pos_import_sync_deletions", "false").lower() == "true",
        "sync_modifications_enabled": db.get_setting("pos_import_sync_modifications", "false").lower() == "true",
    }


@router.post("/api/pos-import/detect-deletions/{import_run_id}")
def detect_deletions(import_run_id: int) -> Any:
    """Dry-run: detect sales that were deleted in the source POS since the last import.

    Returns a summary of missing sales + conflicts + threshold status.
    Does NOT apply any changes — use POST /api/pos-import/apply-deletions to apply.
    """
    from .. import db
    from ..pos_import_sync import detect_deleted_sales, get_pos_import_history
    import os, tempfile, zipfile
    from dbfread import DBF

    # Get the import run record to find the backup file
    with db.conn() as c:
        run = c.execute("SELECT * FROM pos_imports WHERE id=?", (import_run_id,)).fetchone()
    if not run:
        raise HTTPException(404, f"Import run {import_run_id} not found")

    # We need the backup file to extract UNQCODEs — but it was already cleaned up.
    # Instead, get the UNQCODEs from ezi_pos_imports for THIS run + ALL previous runs
    # (cumulative backup means all UNQCODEs should be present)
    with db.conn() as c:
        # Get ALL UNQCODEs from the latest import (this run's invoices)
        # Since each backup is cumulative, the latest import should contain all valid UNQCODEs
        # We need to re-parse the backup file — but it's been cleaned up.
        # Fallback: use the UNQCODEs from ezi_pos_imports for this run as the "known" set
        # and compare against ALL previous UNQCODEs
        current_unqcodes = {row["unqcode"] for row in c.execute(
            "SELECT unqcode FROM ezi_pos_imports WHERE import_run_id=?", (import_run_id,)
        ).fetchall()}

    if not current_unqcodes:
        return {"missing_sales": [], "missing_count": 0, "missing_total_amount": 0,
                "conflicts": [], "conflict_count": 0,
                "message": "No UNQCODEs found for this import run. "
                           "Detection requires the backup file to be re-uploaded."}

    result = detect_deleted_sales(current_unqcodes)
    result["import_run_id"] = import_run_id
    result["feature_flag"] = db.get_setting("pos_import_sync_deletions", "false")
    return result


@router.post("/api/pos-import/apply-deletions")
def apply_deletions(payload: dict) -> Any:
    """Apply the deletion sync — reverses side effects for missing sales.

    Requires:
    - manager_pin: valid manager PIN
    - confirm: must be true
    - missing_sales: list of sale dicts from the dry-run result
    - import_run_id: the import run ID
    """
    from .. import db, shop as shop_mod
    from ..pos_import_sync import apply_deleted_sales_sync

    body = payload or {}
    manager_pin = body.get("manager_pin", "")
    confirm = body.get("confirm", False)
    missing_sales = body.get("missing_sales", [])
    import_run_id = body.get("import_run_id", 0)

    if not confirm:
        raise HTTPException(400, "Confirmation required (set confirm=true)")
    mgr = shop_mod.verify_manager_pin(manager_pin) if manager_pin else None
    if not mgr:
        raise HTTPException(403, "Manager PIN required to apply deletion sync")

    if not missing_sales:
        return {"ok": True, "applied": 0, "skipped": 0, "errors": [],
                "message": "No missing sales to apply"}

    result = apply_deleted_sales_sync(missing_sales, import_run_id)
    result["ok"] = True
    result["manager"] = mgr["name"]
    return result


@router.post("/api/pos-import/detect-modifications/{import_run_id}")
def detect_modifications(import_run_id: int) -> Any:
    """Dry-run: detect sales that were modified in the source POS since the last import.

    Compares checksums of invoice header + line items.
    Returns a summary of modifications + conflicts.
    Does NOT apply any changes.
    """
    from .. import db
    from ..pos_import_sync import detect_modified_sales

    # This endpoint requires re-parsing the backup file
    # For now, return a message explaining the requirement
    return {
        "message": "Modification detection requires re-parsing the backup file. "
                   "This feature will be available when the backup file is re-uploaded "
                   "with the sync flag enabled.",
        "import_run_id": import_run_id,
        "feature_flag": db.get_setting("pos_import_sync_modifications", "false"),
    }


# ════════════════════════════════════════════════════════════════════════════════
# v8.16.7: Expense Deletion Sync — mirrors the sales deletion sync
# ════════════════════════════════════════════════════════════════════════════════

@router.post("/api/pos-import/detect-expense-deletions/{import_run_id}")
def detect_expense_deletions(import_run_id: int) -> Any:
    """Dry-run: detect expenses that were deleted in the source POS since the last import.

    Compares DIARY.DBF hashes from the latest backup against pos_expense_imports.
    Any hash that was previously imported but is missing from the new backup = deleted.

    Returns a summary of missing expenses + threshold status.
    Does NOT apply any changes — use POST /api/pos-import/apply-expense-deletions to apply.
    """
    from .. import db
    from ..pos_import_sync import detect_deleted_expenses
    import os, tempfile, zipfile
    from dbfread import DBF

    # Get the import run record to find the backup file
    with db.conn() as c:
        run = c.execute("SELECT * FROM pos_imports WHERE id=?", (import_run_id,)).fetchone()
    if not run:
        raise HTTPException(404, f"Import run {import_run_id} not found")

    # Re-extract the backup file to get the current set of DIARY.DBF expense hashes
    # The backup file path is stored in pos_imports.backup_path (if available)
    backup_path = dict(run).get("backup_path") if run else None
    if not backup_path or not os.path.exists(backup_path):
        # Try the default data/backups directory
        backup_path_guess = f"data/backups/BU{run['backup_date'].strftime('%Y%m%d')}.zip"
        if os.path.exists(backup_path_guess):
            backup_path = backup_path_guess

    if not backup_path or not os.path.exists(backup_path):
        # Fallback: use the hashes from the current import run as the "known" set
        with db.conn() as c:
            current_hashes = {row["import_hash"] for row in c.execute(
                "SELECT import_hash FROM pos_expense_imports WHERE import_run_id=?",
                (import_run_id,)
            ).fetchall()}
        if not current_hashes:
            return {"missing_expenses": [], "missing_count": 0,
                    "missing_total_amount": 0, "already_synced": 0,
                    "message": "No DIARY.DBF hashes found for this import run. "
                              "Re-upload the backup file to detect deletions."}
    else:
        # Extract DIARY.DBF from the backup zip
        current_hashes = set()
        with tempfile.TemporaryDirectory() as tmp:
            with zipfile.ZipFile(backup_path, "r") as zf:
                zf.extractall(tmp)
            diary_path = os.path.join(tmp, "DIARY.DBF")
            if os.path.exists(diary_path):
                import hashlib
                for rec in DBF(diary_path):
                    details = rec.get("DETAILS", "") or ""
                    if not details:
                        continue
                    h = hashlib.md5(
                        (details + str(rec.get("DATE", ""))).encode()
                    ).hexdigest()
                    current_hashes.add(h)

    result = detect_deleted_expenses(current_hashes)
    result["import_run_id"] = import_run_id
    result["feature_flag"] = db.get_setting("pos_import_sync_expense_deletions", "true")
    return result


@router.post("/api/pos-import/apply-expense-deletions")
def apply_expense_deletions(payload: dict) -> Any:
    """Apply the expense deletion sync — deletes expenses that were removed in the source POS.

    For each missing expense:
    1. Inserts a reversing cash_drawer entry (if it was a cash expense)
    2. Deletes the row from `expenses`
    3. Marks `pos_expense_imports.synced_deleted = 1`

    Requires:
    - manager_pin: valid manager PIN
    - confirm: must be true
    - missing_expenses: list of expense dicts from the dry-run result
    - import_run_id: the import run ID
    """
    from .. import db, shop as shop_mod
    from ..pos_import_sync import apply_deleted_expenses_sync

    body = payload or {}
    manager_pin = body.get("manager_pin", "")
    confirm = body.get("confirm", False)
    missing_expenses = body.get("missing_expenses", [])
    import_run_id = body.get("import_run_id", 0)

    if not confirm:
        raise HTTPException(400, "Confirmation required (set confirm=true)")
    mgr = shop_mod.verify_manager_pin(manager_pin) if manager_pin else None
    if not mgr:
        raise HTTPException(403, "Manager PIN required to apply expense deletion sync")

    if not missing_expenses:
        return {"ok": True, "applied": 0, "skipped": 0, "errors": [],
                "reversed_amount": 0,
                "message": "No missing expenses to apply"}

    result = apply_deleted_expenses_sync(missing_expenses, import_run_id)
    result["ok"] = True
    result["manager"] = mgr["name"]
    return result
