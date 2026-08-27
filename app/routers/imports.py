"""Auto-generated router module — extracted from main.py Phase 1."""
import os, json, time, re, io, csv, secrets, hashlib, traceback
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Request, HTTPException, UploadFile, File, Form, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse, HTMLResponse, RedirectResponse
from pydantic import BaseModel
from typing import Any, Optional

from .. import db
from .. import shop as shop_mod
from .. import insights
from .. import trends as trends_mod
from .. import extract
from .. import reports
from .. import pos_extra
from .. import pos_import
from .. import crypto as crypto_mod
from .. import jobs as jobs_mod
from ..config import BACKUPS, BASE, PAGE_SIZE, PAGES, UPLOADS
from ..export import export_bills, export_insights
from ..ingest import render_pages, save_upload
from ..validate import detect_duplicate, pieces, validate
from ..security import (
    hash_password, verify_password, ensure_password,
    is_logged_in, get_session, get_session_role,
    create_session, delete_session,
    check_login_throttle, record_failed_login,
    SESSION_DAYS,
)

router = APIRouter()

# Backward-compat aliases
_hash_password = hash_password
_verify_password = verify_password
_ensure_password = ensure_password
_is_logged_in = is_logged_in
_get_session = get_session
_get_session_role = get_session_role
_create_session = create_session
_delete_session = delete_session
_check_login_throttle = check_login_throttle
_record_failed_login = record_failed_login

MAX_UPLOAD_BYTES = 200 * 1024 * 1024
MAX_FILES = 100


class CSVImportIn(BaseModel):
    csv_text: str
    type: str  # 'customers' | 'suppliers' | 'categories'




class POSImportPreviewIn(BaseModel):
    csv_text: str
    file_format: str = "csv"




class POSImportRunIn(BaseModel):
    csv_text: str
    file_format: str = "csv"
    mapping: dict
    source_name: str = ""
    filename: str = ""
    import_date: str = ""
    notes: str = ""




@router.post("/api/import/csv")
def import_csv(payload: CSVImportIn) -> Any:
    if payload.type == "customers":
        return pos_extra.import_customers_csv(payload.csv_text)
    elif payload.type == "suppliers":
        return pos_extra.import_suppliers_csv(payload.csv_text)
    elif payload.type == "categories":
        return pos_extra.import_price_categories_csv(payload.csv_text)
    else:
        raise HTTPException(400, "Unknown import type. Use customers, suppliers, or categories.")


# ==================================================================
# Financial reports — Cash Flow + Balance Sheet
# ==================================================================



@router.post("/api/pos-import/preview")
def preview_pos_import(payload: POSImportPreviewIn) -> Any:
    """Parse the uploaded CSV/JSON and return detected columns + first 5 rows for mapping UI."""
    try:
        if payload.file_format == "json":
            data = json.loads(payload.csv_text)
            if isinstance(data, list) and data:
                rows = data
                headers = list(data[0].keys())
            else:
                return {"error": "JSON must be a list of objects"}
        else:
            reader = csv.DictReader(io.StringIO(payload.csv_text))
            rows = list(reader)
            headers = reader.fieldnames or []
        if not rows:
            return {"error": "No data rows found in file"}
        detected = pos_import.detect_columns(headers)
        return {
            "headers": headers,
            "detected_mapping": detected,
            "sample_rows": rows[:5],
            "total_rows": len(rows),
        }
    except Exception as e:
        return {"error": str(e)}




@router.post("/api/pos-import/run")
def run_pos_import(payload: POSImportRunIn) -> Any:
    """Run the actual import using the user-confirmed column mapping."""
    try:
        if payload.file_format == "json":
            rows = json.loads(payload.csv_text)
        else:
            reader = csv.DictReader(io.StringIO(payload.csv_text))
            rows = list(reader)
        result = pos_import.import_pos_backup(
            rows, payload.mapping,
            source_name=payload.source_name,
            filename=payload.filename,
            import_date=payload.import_date,
            notes=payload.notes,
        )
        db.log_activity("pos_import", "pos_import", result.get("import_id"),
                        f"Imported {result['imported_sales']} sales from {payload.source_name or payload.filename}",
                        {"total_revenue": result["total_revenue"]})
        return result
    except Exception as e:
        import traceback
        return {"error": str(e), "traceback": traceback.format_exc()}




@router.get("/api/pos-import/list")
def list_pos_imports() -> Any:
    return {"imports": pos_import.list_imports()}




# v8.4: Changed from /api/pos-import/{import_id} to /api/pos-import/by-id/{import_id}
# to avoid catching /summary, /status, /list, /upload-zip as import_id
@router.get("/api/pos-import/by-id/{import_id}")
def get_pos_import(import_id: int) -> Any:
    imp = pos_import.get_import(import_id)
    if not imp:
        raise HTTPException(404, "import not found")
    return imp




@router.delete("/api/pos-import/by-id/{import_id}")
def delete_pos_import(import_id: int) -> Any:
    """v8.9.1: Uses the canonical pos_import_sync.delete_pos_import() — properly
    reverses stock state, customer stats, commissions, loyalty redemptions,
    and cleans up ezi_pos_imports + pos_expense_imports rows."""
    from ..pos_import_sync import delete_pos_import as sync_delete
    res = sync_delete(import_id)
    if not res.get("ok"):
        raise HTTPException(404, res.get("error", "not found"))
    return res


# ==================================================================
# v8.4: ZIP Upload for POS Backups
# ==================================================================

@router.post("/api/pos-import/upload-zip")
async def upload_zip_pos_import(file: UploadFile = File(...)):
    """Upload a ZIP file containing POS backup data (CSV/JSON/Excel).

    Extracts the ZIP, finds the best data file, parses it, and returns
    detected columns + sample rows — just like /api/pos-import/preview but
    for ZIP uploads.

    The frontend then calls /api/pos-import/run with the extracted content
    to complete the import.
    """
    contents = await file.read()

    # Validate it's actually a ZIP (magic bytes: PK\x03\x04 or PK\x05\x06 for empty)
    if not contents[:2] == b"PK":
        return {"error": "File is not a valid ZIP archive. Please upload a .zip file."}

    try:
        extracted = pos_import.extract_zip_contents(contents)
    except ValueError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"Failed to extract ZIP: {e}"}

    # Parse the extracted file to get headers + rows
    fmt = extracted["file_format"]
    filename = extracted["filename"]
    all_files = extracted["all_files"]

    try:
        if fmt == "json":
            data = json.loads(extracted["content"])
            if not isinstance(data, list) or not data:
                return {"error": "JSON file inside ZIP must be a list of objects"}
            rows = data
            headers = list(data[0].keys()) if data else []
        elif fmt == "excel":
            headers, rows = pos_import.parse_excel_bytes(extracted["raw_bytes"], filename)
        else:  # csv
            reader = csv.DictReader(io.StringIO(extracted["content"]))
            rows = list(reader)
            headers = reader.fieldnames or []

        if not rows:
            return {"error": f"No data rows found in '{filename}' inside the ZIP"}

        detected = pos_import.detect_columns(headers)
        return {
            "headers": headers,
            "detected_mapping": detected,
            "sample_rows": rows[:5],
            "total_rows": len(rows),
            "file_format": fmt,
            "csv_text": extracted["content"] if fmt != "excel" else "",
            "filename": filename,
            "all_files_in_zip": all_files,
            "zip_filename": file.filename,
        }
    except Exception as e:
        return {"error": f"Failed to parse '{filename}': {e}"}




@router.get("/api/pos-import/today/summary")
def today_import_summary(date: str = "") -> Any:
    """Combined summary of today's sales — native + imported from external POS."""
    return pos_import.get_today_summary_from_imports(date)


# ==================================================================
# Sales Targets
# ==================================================================



# ═══════════════════════════════════════════════════
# Ezi POS DBF Backup Import
# ═══════════════════════════════════════════════════

@router.post("/api/ezi-import/preview")
async def ezi_preview(file: UploadFile = File(...)):
    """Preview an Ezi POS backup ZIP file."""
    import tempfile, os
    from ..ezi_import import preview_ezi_backup
    
    # Save uploaded file
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.zip')
    content = await file.read()
    tmp.write(content)
    tmp.close()
    
    try:
        summary = preview_ezi_backup(tmp.name)
        return summary
    except Exception as e:
        raise HTTPException(400, f"Cannot read backup: {e}")
    finally:
        os.unlink(tmp.name)


@router.post("/api/ezi-import/run")
async def ezi_import_run(file: UploadFile = File(...)):
    """Import an Ezi POS backup ZIP into BillBook."""
    import tempfile, os
    from ..ezi_import import import_ezi_backup
    
    # Save uploaded file
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.zip')
    content = await file.read()
    tmp.write(content)
    tmp.close()
    
    try:
        results = import_ezi_backup(tmp.name, db.conn)
        db.log_activity("ezi_import", "system", 0,
                        f"Ezi POS import: {results['sales']} sales, {results['sale_items']} items, {results['customers']} customers",
                        results)
        return results
    except Exception as e:
        raise HTTPException(500, f"Import failed: {str(e)}")
    finally:
        os.unlink(tmp.name)
