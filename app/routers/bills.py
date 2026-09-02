"""Auto-generated router module — extracted from main.py Phase 1."""
import logging
import os, json, time, re, io, csv, traceback, asyncio
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Request, HTTPException, UploadFile, File, Form, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, field_validator
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
from .. import profit as profit_mod  # v5.0 Phase 1: running weighted avg cost
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

logger = logging.getLogger(__name__)

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

# v8.13.2: Reduced from 200MB → 20MB per file (200MB was causing memory
# exhaustion — the data was loaded entirely into RAM before the size check).
# 20MB is more than enough for a high-res PDF or photo of a bill.
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_FILES = 25  # v8.13.2: Reduced from 100 → 25 (100 was a DoS vector)

# v8.13.2: Magic-byte signatures for file-type validation.
# Validates that the file content matches the extension — prevents attackers
# from uploading a renamed .exe or polyglot file targeting PyMuPDF/Pillow RCE.
FILE_MAGIC_BYTES = {
    b"\x25PDF": {".pdf"},                              # %PDF
    b"\x89PNG\r\n\x1a\n": {".png"},                    # PNG signature
    b"\xff\xd8\xff": {".jpg", ".jpeg"},                # JPEG SOI marker
    b"RIFF": {".webp"},                                # WebP (RIFF....WEBP)
}

# Required magic-byte prefixes per extension
EXT_REQUIRED_MAGIC = {
    ".pdf": [b"%PDF"],
    ".png": [b"\x89PNG\r\n\x1a\n"],
    ".jpg": [b"\xff\xd8\xff"],
    ".jpeg": [b"\xff\xd8\xff"],
    ".webp": [b"RIFF"],
}


def _validate_magic_bytes(data: bytes, filename: str) -> None:
    """Validate that the file's magic bytes match its extension.

    SECURITY (v8.13.2): Extension-only validation is insufficient — an attacker
    can rename a malicious .exe to .pdf and bypass the check. This function
    reads the first few bytes and verifies they match the expected magic bytes
    for the claimed file type.
    """
    ext = Path(filename or "").suffix.lower()
    required = EXT_REQUIRED_MAGIC.get(ext)
    if not required:
        return  # No magic-byte requirement for this extension
    for magic in required:
        if data.startswith(magic):
            return  # Match — valid
    raise HTTPException(
        400,
        f"File content does not match extension '{ext}' for '{filename}'. "
        f"Magic bytes validation failed — the file may be corrupted or misnamed."
    )




class PatchBill(BaseModel):
    """Inline-editable fields for quick updates from the list view."""
    supplier_name: str | None = None
    phone: str | None = None
    bill_date: str | None = None
    bill_no: str | None = None
    written_total: float | None = None
    payment_status: str | None = None
    credit_due_date: str | None = None






class PaymentMethodIn(BaseModel):
    name: str
    type: str = "cash"
    icon: str = "💵"
    sort_order: int = 0






class ProviderIn(BaseModel):
    name: str
    provider_type: str  # 'gemini' | 'groq' | 'openrouter'
    api_key: str
    model: str = ""
    priority: int = 0
    enabled: bool = True

    # v8.18.4 FIX: keys pasted from password managers / docs often carry
    # leading/trailing whitespace. Sent as-is it either crashes httpx
    # ("Illegal header value") or is rejected by the provider. Strip once,
    # centrally, at the API boundary.
    @field_validator("api_key", "model", "name", mode="before")
    @classmethod
    def _strip_ws(cls, v):
        return v.strip() if isinstance(v, str) else v







class TestProviderIn(BaseModel):
    """Test a provider config without saving it."""
    provider_type: str
    api_key: str
    model: str = ""

    @field_validator("api_key", "model", mode="before")
    @classmethod
    def _strip_ws(cls, v):
        return v.strip() if isinstance(v, str) else v




def _validate_upload(files: list[UploadFile]):
    """Pre-flight check: sizes, counts, extensions."""
    if not files:
        raise HTTPException(400, "No files uploaded")
    if len(files) > MAX_FILES:
        raise HTTPException(400, f"Too many files (max {MAX_FILES})")
    allowed_ext = {".pdf", ".png", ".jpg", ".jpeg", ".webp"}
    for f in files:
        ext = Path(f.filename or "").suffix.lower()
        if ext not in allowed_ext:
            raise HTTPException(400, f"Unsupported file type: {ext} ({f.filename})")








class ItemIn(BaseModel):
    raw: str = ""
    item_code: str = ""
    price: float = 0
    qty: float = 0
    unit: str = "pcs"
    category_id: int | None = None
    page_no: int | None = None






class ConfirmIn(BaseModel):
    supplier_name: str = ""
    phone: str = ""
    bill_date: str = ""
    bill_no: str = ""
    written_total: float | None = None
    payment_status: str = "paid"
    credit_due_date: str | None = None
    notes: str = ""
    items: list[ItemIn] = []


def _flag_text(f) -> str:
    """v8.18.6: bill flags must be plain strings — the UI renders them with a
    plain string interpolation, so a dict flag shows as '[object Object]' in
    the yellow warning alerts on the edit-bill page.

    Cost-overrun warnings from check_bill_cost_vs_cheapest_supplier() were
    stored as structured dicts (with a human-readable .message field) by the
    confirm endpoint. This helper flattens any dict flag to its message text:
    new writes store strings, and rows already in existing databases are
    healed on read (get_bill / list_bills) and on re-confirm / add-pages.
    """
    if isinstance(f, dict):
        for key in ("message", "msg", "text", "warning", "error"):
            v = f.get(key)
            if v:
                return str(v)
        try:
            return json.dumps(f, ensure_ascii=False)
        except Exception:
            return "warning"
    return str(f)





@router.post("/api/upload")
def upload(request: Request, files: list[UploadFile] = File(...)):
    """Sync upload — single shot. For large/multi-file uploads use /api/upload-async.

    Kept for backward compatibility; the frontend uses the async version.

    SECURITY (v8.13.2): Added magic-byte validation + Content-Length early
    rejection (prevents loading 200MB into RAM before the size check).
    """
    # v8.13.2 M4: Early Content-Length check — reject before loading into memory
    content_length = int(request.headers.get("content-length", 0))
    if content_length > MAX_UPLOAD_BYTES * MAX_FILES + 10 * 1024 * 1024:
        # Allow some overhead for multipart encoding (~10MB)
        raise HTTPException(413, f"Request too large (max {MAX_UPLOAD_BYTES * MAX_FILES // (1024*1024)}MB total)")
    _validate_upload(files)
    with db.conn() as c:
        bill_id = c.execute("INSERT INTO bills DEFAULT VALUES").lastrowid
    pages = []
    page_counter = 0  # v8.18.5: running page counter — a multi-page PDF must
                       # not collapse all its pages onto one page number
    for i, f in enumerate(files):
        # v8.13.2 M4: Read with size cap to avoid loading huge files into memory
        data = f.file.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, f"File too large: {f.filename} (max {MAX_UPLOAD_BYTES // (1024*1024)}MB)")
        # v8.13.2 M3: Magic-byte validation — content must match extension
        _validate_magic_bytes(data, f.filename or "upload")
        saved = save_upload(data, f.filename or "upload", UPLOADS)
        for p in render_pages(saved, PAGES, stem_prefix=saved.stem):
            page_counter += 1
            with db.conn() as c:
                c.execute(
                    "INSERT INTO bill_pages(bill_id, filename, page_no) VALUES(?,?,?)",
                    (bill_id, p.name, page_counter),
                )
            pages.append(p)
        # v8.2.3: Delete the original uploaded file — we only need the rendered pages
        try:
            saved.unlink(missing_ok=True)
        except Exception as _e:
            logger.warning("Silent exception in bills.py: %s", _e, exc_info=True)
    try:
        ex, provider = extract.extract(pages)
        v = validate(ex)
    except Exception as e:
        ex, provider = None, None
        v = {
            "items": [], "flags": [f"extraction failed: {e} — manual entry needed"],
            "status": "review", "computed_total": None, "unit": "pcs",
            "written_total": None, "phone": None, "supplier_guess": None,
            "bill_date": None,
        }

    with db.conn() as c:
        for it in v["items"]:
            c.execute(
                "INSERT INTO bill_items(bill_id, raw, price, qty, unit, line_total, confidence, "
                "sell_price, category_id, page_no) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (bill_id, it["raw"], it["price"], it["qty"], it["unit"],
                 it["line_total"], it.get("structural_confidence") or it.get("confidence"),
                 it.get("sell_price"), it.get("category_id"), it.get("page_no")),
            )
        c.execute(
            "UPDATE bills SET supplier_name=?, phone=?, bill_date=?, written_total=?, "
            "computed_total=?, unit=?, status=?, flags=?, extraction=?, provider=? WHERE id=?",
            (v["supplier_guess"], v["phone"], v["bill_date"], v["written_total"],
             v["computed_total"], v["unit"], v["status"], json.dumps(v["flags"]),
             json.dumps(ex) if ex else None, provider, bill_id),
        )
    # v8.16.0: Add confidence info to response
    return {"id": bill_id, "status": v["status"], "flags": v["flags"],
            "overall_confidence": v.get("overall_confidence", 1.0),
            "review_item_count": v.get("review_item_count", 0)}


# ----------------- ASYNC UPLOAD (with progress SSE) -----------------



@router.post("/api/upload-async")
async def upload_async(request: Request, files: list[UploadFile] = File(...)):
    """Start an async upload + extraction job. Returns job_id + bill_id immediately.

    Poll GET /api/jobs/{job_id} or stream GET /api/jobs/{job_id}/stream for progress.

    SECURITY (v8.13.2): Added magic-byte validation + Content-Length early
    rejection + capped per-file read at MAX_UPLOAD_BYTES+1.
    """
    # v8.13.2 M4: Early Content-Length check
    content_length = int(request.headers.get("content-length", 0))
    if content_length > MAX_UPLOAD_BYTES * MAX_FILES + 10 * 1024 * 1024:
        raise HTTPException(413, f"Request too large (max {MAX_UPLOAD_BYTES * MAX_FILES // (1024*1024)}MB total)")
    _validate_upload(files)

    # Read all file bytes upfront (UploadFile streams close after request)
    file_data = []
    total_size = 0
    for f in files:
        # v8.13.2 M4: Read with size cap
        data = await f.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, f"File too large: {f.filename} (max {MAX_UPLOAD_BYTES // (1024*1024)}MB)")
        # v8.13.2 M3: Magic-byte validation
        _validate_magic_bytes(data, f.filename or "upload")
        total_size += len(data)
        file_data.append({"name": f.filename or "upload", "data": data, "size": len(data)})

    # v8.13.2: Reduced total from 500MB → MAX_UPLOAD_BYTES * MAX_FILES (500MB)
    if total_size > MAX_UPLOAD_BYTES * MAX_FILES:
        raise HTTPException(413, "Total upload too large (max 500 MB)")

    # Create the bill row upfront so user can navigate even mid-job
    with db.conn() as c:
        bill_id = c.execute("INSERT INTO bills DEFAULT VALUES").lastrowid

    job = jobs_mod.create_job("upload", bill_id=bill_id)
    job.emit("queued", f"Received {len(file_data)} file(s), {total_size // 1024} KB total", progress=5)

    # Start the background task
    asyncio.create_task(_run_upload_job(job, bill_id, file_data))

    return {"job_id": job.id, "bill_id": bill_id}


async def _run_upload_job(job: jobs_mod.Job, bill_id: int, file_data: list[dict]):
    """Background coroutine: render pages, extract via AI, validate, save.

    Emits progress events throughout. Catches all errors so the job always
    terminates in 'done' or 'error' state.
    """
    try:
        job.status = "running"

        # --- Phase 1: Save uploads & render pages ---
        job.emit("uploading", "Saving uploaded files...", progress=10)
        all_pages = []
        page_no = 0
        for fi, f in enumerate(file_data):
            saved = save_upload(f["data"], f["name"], UPLOADS)
            job.emit("rendering", f"Rendering {f['name']}...", progress=10 + int(15 * fi / max(len(file_data), 1)))

            # Run sync render in a thread to avoid blocking the event loop
            def _render():
                return render_pages(saved, PAGES, stem_prefix=saved.stem)
            pages = await asyncio.to_thread(_render)
            for p in pages:
                page_no += 1
                with db.conn() as c:
                    c.execute(
                        "INSERT INTO bill_pages(bill_id, filename, page_no) VALUES(?,?,?)",
                        (bill_id, p.name, page_no),
                    )
                all_pages.append(p)
            # v8.2.3: Delete the original uploaded file — we only need the rendered pages
            try:
                saved.unlink(missing_ok=True)
            except Exception as _e:
                logger.warning("Silent exception in bills.py: %s", _e, exc_info=True)
            job.emit("rendering", f"Rendered {len(pages)} page(s) from {f['name']} ({page_no} total)",
                     progress=10 + int(25 * (fi + 1) / max(len(file_data), 1)))

        if not all_pages:
            raise RuntimeError("No pages were rendered from the uploaded files")

        job.emit("rendering", f"Total: {len(all_pages)} pages ready for AI extraction", progress=35)

        # --- Phase 2: AI extraction (chunked for large page counts) ---
        job.emit("extracting", f"Starting AI extraction ({len(all_pages)} pages)...", progress=40)

        try:
            # Run extraction in a thread (httpx is sync)
            # Pass a progress callback that emits job events per chunk
            def _extract():
                def on_progress(chunk_label, chunk_idx, total_chunks):
                    # Calculate progress: 40% (start) to 80% (end) across chunks
                    pct = 40 + int(40 * (chunk_idx - 1) / max(total_chunks, 1))
                    job.emit("extracting", f"Extracting {chunk_label} ({chunk_idx}/{total_chunks})...",
                             progress=pct)
                return extract.extract(all_pages, on_progress=on_progress)
            ex, provider = await asyncio.to_thread(_extract)
        except Exception as e:
            job.emit("extracting", f"AI extraction failed: {e}", level="error")
            ex, provider = None, None
            v = {
                "items": [],
                "flags": [f"AI extraction failed: {e}. Manual entry needed."],
                "status": "review", "computed_total": None, "unit": "pcs",
                "written_total": None, "phone": None, "supplier_guess": None,
                "bill_date": None,
            }
        else:
            job.emit("extracting", f"AI returned {len(ex.get('lines') or [])} line(s) via {provider}", progress=80, level="success")
            try:
                v = validate(ex)
            except Exception as e:
                job.emit("validating", f"Validation error: {e}", level="warning")
                v = {
                    "items": [], "flags": [f"validation error: {e}"],
                    "status": "review", "computed_total": None, "unit": "pcs",
                    "written_total": ex.get("written_total") if ex else None,
                    "phone": ex.get("phone") if ex else None,
                    "supplier_guess": ex.get("supplier_guess") if ex else None,
                    "bill_date": ex.get("bill_date") if ex else None,
                }

        # --- Phase 3: Save to DB ---
        job.emit("saving", "Saving extracted data to database...", progress=90)
        # Load all categories for sell-price matching
        with db.conn() as c:
            all_cats = [dict(r) for r in c.execute(
                "SELECT id, sell_price FROM price_categories WHERE active=1 ORDER BY sell_price ASC"
            ).fetchall()]
            default_cat_id = None
            cat_row = c.execute(
                "SELECT id FROM price_categories WHERE sell_price=250 AND active=1 LIMIT 1"
            ).fetchone()
            if cat_row:
                default_cat_id = cat_row["id"]

        def match_category(sell_price):
            """Match a sell price to the closest category. If no sell price, default to 250."""
            if not sell_price or sell_price <= 0:
                return default_cat_id
            # Find exact match first
            for cat in all_cats:
                if abs(cat["sell_price"] - sell_price) < 1:
                    return cat["id"]
            # Find closest category (round up to nearest category)
            closest = None
            min_diff = float('inf')
            for cat in all_cats:
                diff = abs(cat["sell_price"] - sell_price)
                if diff < min_diff:
                    min_diff = diff
                    closest = cat["id"]
            return closest

        with db.conn() as c:
            for it in v["items"]:
                # Use AI-extracted sell price to match category, or default
                cat_id = match_category(it.get("sell_price"))
                c.execute(
                    "INSERT INTO bill_items(bill_id, raw, price, qty, unit, line_total, confidence, category_id, page_no) "
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    (bill_id, it["raw"], it["price"], it["qty"], it["unit"],
                     it["line_total"], it.get("confidence"), cat_id, it.get("page_no")),
                )
            c.execute(
                "UPDATE bills SET supplier_name=?, phone=?, bill_date=?, written_total=?, "
                "computed_total=?, unit=?, status=?, flags=?, extraction=?, provider=? WHERE id=?",
                (v["supplier_guess"], v["phone"], v["bill_date"], v["written_total"],
                 v["computed_total"], v["unit"], v["status"], json.dumps(v["flags"]),
                 json.dumps(ex) if ex else None, provider, bill_id),
            )

        job.result = {
            "bill_id": bill_id,
            "status": v["status"],
            "flags": v["flags"],
            "items_count": len(v["items"]),
            "provider": provider,
        }
        job.status = "done"
        job.emit("done", f"Done! {len(v['items'])} item(s) extracted. "
                  f"{'Review needed.' if v['flags'] else 'All checks passed.'}",
                  level="success", progress=100)
        # Log activity
        sup_name = v.get("supplier_guess") or "Unknown supplier"
        db.log_activity(
            "bill_created", "bill", bill_id,
            f"Uploaded bill #{bill_id} from {sup_name} ({len(v['items'])} items)",
            {"supplier": sup_name, "items": len(v["items"]), "provider": provider,
             "extracted": provider is not None},
        )

    except Exception as e:
        job.status = "error"
        job.error = str(e)
        job.emit("error", f"Job failed: {e}", level="error", progress=100)
        # Log full traceback for debugging
        traceback.print_exc()




@router.get("/api/jobs/{job_id}")
def get_job_status(job_id: str) -> Any:
    """Poll job status. Returns full job state including recent events."""
    job = jobs_mod.get_job(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    return job.to_dict()




@router.get("/api/jobs/{job_id}/stream")
async def stream_job(job_id: str, request: Request):
    """Server-Sent Events stream for real-time job progress.

    Client subscribes with: `new EventSource('/api/jobs/JOB_ID/stream')`
    Each event is JSON: {ts, stage, message, level, progress}
    """
    job = jobs_mod.get_job(job_id)
    if not job:
        raise HTTPException(404, "job not found")

    q = job.subscribe()

    async def event_stream():
        try:
            # First, replay all past events
            for ev in job.events:
                yield f"data: {json.dumps({'ts': ev.ts, 'stage': ev.stage, 'message': ev.message, 'level': ev.level, 'progress': ev.progress, 'status': job.status})}\n\n"
                await asyncio.sleep(0)
            # If already finished, send terminal marker and stop
            if job.status in ("done", "error"):
                yield f"data: {json.dumps({'terminal': True, 'status': job.status, 'result': job.result, 'error': job.error})}\n\n"
                return
            # Stream new events
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"data: {json.dumps({'ts': ev.ts, 'stage': ev.stage, 'message': ev.message, 'level': ev.level, 'progress': ev.progress, 'status': job.status})}\n\n"
                    if job.status in ("done", "error"):
                        yield f"data: {json.dumps({'terminal': True, 'status': job.status, 'result': job.result, 'error': job.error})}\n\n"
                        break
                except asyncio.TimeoutError:
                    # Heartbeat to keep connection alive
                    yield ": heartbeat\n\n"
        finally:
            job.unsubscribe(q)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )




@router.post("/api/bills/{bill_id}/add-pages")
async def add_pages(request: Request, bill_id: int, files: list[UploadFile] = File(...)):
    """Add more images to an existing bill, then AI-extract their items.

    v8.18.5 FIX (user report: "on edit bill when user adds an image it doesn't
    add and its items are not extracted"):
      1. The old handler only saved the rendered pages — it NEVER called the
         AI extractor, so no bill_items rows were created for the new pages.
      2. Page numbers collided: every rendered page of file i got
         page_no = existing_max + i + 1 (file index, not page counter), so a
         multi-page PDF collapsed onto a single page number.
      3. It was synchronous with no progress feedback and the frontend
         re-navigated to the SAME hash (which doesn't re-render), so the new
         image never appeared on screen.

    Now an async job (same pattern as /api/upload-async): renders pages with a
    running counter, extracts ONLY the new pages, merges items into the bill
    (page numbers offset past the existing pages), and streams progress.
    Returns {job_id, bill_id}; stream GET /api/jobs/{job_id}/stream.
    """
    # Early Content-Length check — reject before loading into memory
    content_length = int(request.headers.get("content-length", 0))
    if content_length > MAX_UPLOAD_BYTES * MAX_FILES + 10 * 1024 * 1024:
        raise HTTPException(413, f"Request too large (max {MAX_UPLOAD_BYTES * MAX_FILES // (1024*1024)}MB total)")
    _validate_upload(files)

    with db.conn() as c:
        if not c.execute(
            "SELECT 1 FROM bills WHERE id=? AND deleted_at IS NULL", (bill_id,)
        ).fetchone():
            raise HTTPException(404, "bill not found")
        row = c.execute(
            "SELECT MAX(page_no) AS m FROM bill_pages WHERE bill_id=?", (bill_id,)
        ).fetchone()
    existing_max = row["m"] or 0

    # Read all file bytes upfront (UploadFile streams close after the request)
    file_data = []
    for f in files:
        data = await f.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, f"File too large: {f.filename} (max {MAX_UPLOAD_BYTES // (1024*1024)}MB)")
        _validate_magic_bytes(data, f.filename or "upload")
        file_data.append({"name": f.filename or "upload", "data": data})

    job = jobs_mod.create_job("add-pages", bill_id=bill_id)
    job.emit("queued", f"Adding {len(file_data)} file(s) to bill #{bill_id}...", progress=5)
    asyncio.create_task(_run_add_pages_job(job, bill_id, file_data, existing_max))
    return {"job_id": job.id, "bill_id": bill_id}


async def _run_add_pages_job(job: jobs_mod.Job, bill_id: int,
                             file_data: list[dict], existing_max: int):
    """Background job for add-pages: render pages → AI-extract the NEW pages
    only → merge items + bill fields into the existing bill.

    Catches all errors so the job always terminates in 'done' or 'error'.
    """
    added_pages = 0
    try:
        job.status = "running"

        # --- Phase 1: render pages with a RUNNING page counter ---
        job.emit("uploading", "Saving and rendering pages...", progress=10)
        new_pages = []
        page_no = existing_max
        for fi, f in enumerate(file_data):
            saved = save_upload(f["data"], f["name"], UPLOADS)
            job.emit("rendering", f"Rendering {f['name']}...",
                     progress=10 + int(15 * fi / max(len(file_data), 1)))

            def _render():
                return render_pages(saved, PAGES, stem_prefix=saved.stem)
            pages = await asyncio.to_thread(_render)
            for p in pages:
                page_no += 1
                with db.conn() as c:
                    c.execute(
                        "INSERT INTO bill_pages(bill_id, filename, page_no) VALUES(?,?,?)",
                        (bill_id, p.name, page_no),
                    )
                new_pages.append(p)
            added_pages += len(pages)
            # v8.2.3: Delete the original uploaded file — only rendered pages matter
            try:
                saved.unlink(missing_ok=True)
            except Exception as _e:
                logger.warning("Silent exception in bills.py: %s", _e, exc_info=True)
            job.emit("rendering", f"Rendered {len(pages)} page(s) from {f['name']} "
                     f"({page_no} total pages on this bill)",
                     progress=10 + int(25 * (fi + 1) / max(len(file_data), 1)))

        if not new_pages:
            raise RuntimeError("No pages were rendered from the uploaded files")

        # --- Phase 2: AI extraction on ONLY the new pages ---
        job.emit("extracting", f"Extracting {len(new_pages)} new page(s) with AI...", progress=40)
        ex, provider = None, None
        new_flags: list[str] = []
        items: list[dict] = []
        try:
            def _extract():
                def on_progress(chunk_label, chunk_idx, total_chunks):
                    pct = 40 + int(35 * (chunk_idx - 1) / max(total_chunks, 1))
                    job.emit("extracting", f"Extracting {chunk_label} ({chunk_idx}/{total_chunks})...",
                             progress=pct)
                return extract.extract(new_pages, on_progress=on_progress)
            ex, provider = await asyncio.to_thread(_extract)
            v = validate(ex)
            items = v["items"]
            new_flags = v["flags"]
        except Exception as e:
            # Extraction failure is NOT fatal — pages are already saved; the
            # user can enter the items manually on the edit page.
            job.emit("extracting", f"AI extraction failed: {e} — pages added, enter items manually",
                     level="warning")
            new_flags = [f"AI extraction failed for added pages: {e} — manual entry needed"]

        # --- Phase 3: merge items into the existing bill ---
        job.emit("saving", "Merging extracted items into the bill...", progress=85)
        extracted_count = 0
        with db.conn() as c:
            # Category matcher (same rules as the upload flow)
            all_cats = [dict(r) for r in c.execute(
                "SELECT id, sell_price FROM price_categories WHERE active=1 ORDER BY sell_price ASC"
            ).fetchall()]
            default_cat_id = None
            cat_row = c.execute(
                "SELECT id FROM price_categories WHERE sell_price=250 AND active=1 LIMIT 1"
            ).fetchone()
            if cat_row:
                default_cat_id = cat_row["id"]

            def match_category(sell_price):
                if not sell_price or sell_price <= 0:
                    return default_cat_id
                for cat in all_cats:
                    if abs(cat["sell_price"] - sell_price) < 1:
                        return cat["id"]
                closest, min_diff = None, float("inf")
                for cat in all_cats:
                    diff = abs(cat["sell_price"] - sell_price)
                    if diff < min_diff:
                        min_diff, closest = diff, cat["id"]
                return closest

            for it in items:
                # Items from the new batch reference page 1..N of the batch —
                # shift them past the bill's existing pages so "View image"
                # jumps to the right page.
                it_page = (it.get("page_no") or 1) + existing_max
                cat_id = match_category(it.get("sell_price"))
                c.execute(
                    "INSERT INTO bill_items(bill_id, raw, price, qty, unit, line_total, confidence, "
                    "sell_price, category_id, page_no) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (bill_id, it["raw"], it["price"], it["qty"], it["unit"],
                     it["line_total"], it.get("confidence"), it.get("sell_price"),
                     cat_id, it_page),
                )
                extracted_count += 1

            # Merge bill-level fields — NEVER overwrite what the user already
            # has (they may have edited supplier/date/total on this bill).
            bill = c.execute(
                "SELECT supplier_name, phone, bill_date, written_total, flags, provider "
                "FROM bills WHERE id=?", (bill_id,)
            ).fetchone()
            if bill:
                try:
                    old_flags = json.loads(bill["flags"] or "[]")
                except Exception:
                    old_flags = []
                summary = f"Added {added_pages} page(s)"
                if extracted_count:
                    summary += f" — {extracted_count} item(s) extracted via {provider}"
                else:
                    summary += " — no items extracted, enter them manually"
                merged_flags = [_flag_text(f) for f in old_flags] + new_flags + [summary]
                # Recompute the computed total across ALL items (old + new)
                row = c.execute(
                    "SELECT COALESCE(SUM(line_total), 0) AS t FROM bill_items WHERE bill_id=?",
                    (bill_id,),
                ).fetchone()
                c.execute(
                    "UPDATE bills SET "
                    "supplier_name=COALESCE(NULLIF(supplier_name,''),?), "
                    "phone=COALESCE(NULLIF(phone,''),?), "
                    "bill_date=COALESCE(bill_date,?), "
                    "written_total=COALESCE(written_total,?), "
                    "computed_total=?, status='review', flags=?, "
                    "provider=COALESCE(provider,?) WHERE id=?",
                    (
                        (ex or {}).get("supplier_guess") or "",
                        (ex or {}).get("phone") or "",
                        (ex or {}).get("bill_date"),
                        (ex or {}).get("written_total"),
                        row["t"],
                        json.dumps(merged_flags),
                        provider,
                        bill_id,
                    ),
                )

        job.result = {
            "bill_id": bill_id,
            "added_pages": added_pages,
            "items_extracted": extracted_count,
            "provider": provider,
        }
        job.status = "done"
        job.emit("done", f"Done! {added_pages} page(s) added, {extracted_count} item(s) extracted.",
                 level="success", progress=100)
        db.log_activity(
            "bill_pages_added", "bill", bill_id,
            f"Added {added_pages} page(s) to bill #{bill_id} ({extracted_count} item(s) extracted)",
            {"added_pages": added_pages, "items_extracted": extracted_count,
             "provider": provider},
        )
    except Exception as e:
        job.status = "error"
        job.error = str(e)
        job.emit("error", f"Failed to add pages: {e} "
                 f"({added_pages} page(s) were added before the failure)",
                 level="error", progress=100)
        traceback.print_exc()


# ------------------------------------------------------------------
# Item search — find all purchases of a specific item across bills
# ------------------------------------------------------------------



@router.get("/api/items/bills")
def items_bills_list(q: str = "", start: str = "", end: str = "",
                      page: int = 1, page_size: int = 50) -> Any:
    """v8.7: Bill list with item aggregates for the #/items master-detail view.

    Returns bill headers + item_count + category_count + total_cost (sum of
    line_totals). Does NOT return items — caller fetches them on expand via
    GET /api/bills/{bill_id}.

    Search (q): matches supplier_name, bill_no, OR any bill_item.raw/item_code
    text (so searching 'toy' finds bills containing 'toy' items).
    """
    page = max(1, page)
    page_size = min(max(1, page_size), 200)
    pattern = f"%{q}%" if q.strip() else None

    with db.conn() as c:
        # Build WHERE clause
        where_clauses = ["b.deleted_at IS NULL", "b.status IN ('confirmed', 'review')"]
        params = []
        if pattern:
            where_clauses.append(
                "(b.supplier_name LIKE ? OR b.bill_no LIKE ? "
                "OR EXISTS (SELECT 1 FROM bill_items bi2 WHERE bi2.bill_id = b.id "
                "           AND (bi2.raw LIKE ? OR bi2.item_code LIKE ?)))"
            )
            params.extend([pattern, pattern, pattern, pattern])
        if start:
            where_clauses.append("COALESCE(b.bill_date, date(b.created_at)) >= ?")
            params.append(start)
        if end:
            where_clauses.append("COALESCE(b.bill_date, date(b.created_at)) <= ?")
            params.append(end)
        where_sql = " AND ".join(where_clauses)

        # Count total matching bills
        total = c.execute(
            f"SELECT COUNT(*) AS n FROM bills b WHERE {where_sql}",
            params,
        ).fetchone()["n"]

        # v8.19.1: clamp the page (last-page deletion / filter shrink)
        page = db.clamp_page(page, total, page_size)

        # Fetch bill headers + aggregates in one query (using a subquery for aggregates)
        rows = c.execute(
            f"SELECT b.id, b.supplier_name, b.phone, b.bill_date, b.bill_no, "
            f"b.written_total, b.computed_total, b.payment_status, b.status, "
            f"(SELECT COUNT(*) FROM bill_items bi WHERE bi.bill_id = b.id) AS item_count, "
            f"(SELECT COUNT(DISTINCT bi.category_id) FROM bill_items bi WHERE bi.bill_id = b.id "
            f" AND bi.category_id IS NOT NULL) AS category_count, "
            f"(SELECT COALESCE(SUM(bi.line_total), 0) FROM bill_items bi WHERE bi.bill_id = b.id) AS total_cost "
            f"FROM bills b WHERE {where_sql} "
            f"ORDER BY COALESCE(b.bill_date, date(b.created_at)) DESC, b.id DESC "
            f"LIMIT ? OFFSET ?",
            params + [page_size, (page - 1) * page_size],
        ).fetchall()

    return {
        "bills": [dict(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages_total": (total + page_size - 1) // page_size if total > 0 else 0,
    }


@router.get("/api/items/recent")
def recent_items(limit: int = 20) -> Any:
    """Get recent bill items across all bills (both review and confirmed).

    v8.4: Used by the Item Search page to show recent items when no search
    query is entered — helps users discover content immediately.
    """
    limit = min(max(1, limit), 100)
    with db.conn() as c:
        total = c.execute(
            "SELECT COUNT(*) AS n FROM bill_items bi "
            "JOIN bills b ON bi.bill_id = b.id "
            "WHERE b.deleted_at IS NULL AND b.status IN ('confirmed', 'review')"
        ).fetchone()["n"]
        rows = c.execute(
            "SELECT bi.id, bi.raw, bi.item_code, bi.price, bi.qty, bi.unit, bi.line_total, "
            "bi.category_id, pc.name AS cat_name, pc.sell_price, "
            "b.id AS bill_id, b.supplier_name, b.bill_date, b.payment_status, b.status AS bill_status "
            "FROM bill_items bi "
            "JOIN bills b ON bi.bill_id = b.id "
            "LEFT JOIN price_categories pc ON bi.category_id = pc.id "
            "WHERE b.deleted_at IS NULL AND b.status IN ('confirmed', 'review') "
            "ORDER BY b.bill_date DESC, bi.id DESC LIMIT ?",
            (limit,)
        ).fetchall()
    return {"items": [dict(r) for r in rows], "total": total}


@router.get("/api/items/search")
def search_items(q: str, page: int = 1, page_size: int = 25) -> Any:
    """Search across all bill items by name. Returns purchases with bill context.

    v8.4: Includes both 'review' and 'confirmed' bills so items show up
    immediately after upload — even before the bill is confirmed.
    """
    if not q.strip():
        return {"items": [], "total": 0, "page": page, "pages_total": 0}
    pattern = f"%{q}%"
    base_where = (
        "FROM bill_items bi "
        "JOIN bills b ON bi.bill_id = b.id "
        "LEFT JOIN price_categories pc ON bi.category_id = pc.id "
        "WHERE b.deleted_at IS NULL AND b.status IN ('confirmed', 'review') "
        "AND (bi.raw LIKE ? OR bi.item_code LIKE ?)"
    )
    select_cols = (
        "bi.id, bi.raw, bi.item_code, bi.price, bi.qty, bi.unit, bi.line_total, "
        "bi.category_id, pc.name AS cat_name, pc.sell_price, "
        "b.id AS bill_id, b.supplier_name, b.bill_date, b.payment_status, b.status AS bill_status "
    )
    with db.conn() as c:
        total = c.execute(
            f"SELECT COUNT(*) AS n {base_where}", (pattern, pattern)
        ).fetchone()["n"]
        # v8.19.1: clamp the page (last-page deletion / filter shrink)
        page = db.clamp_page(page, total, page_size)
        rows = c.execute(
            f"SELECT {select_cols} {base_where} ORDER BY b.bill_date DESC, bi.id LIMIT ? OFFSET ?",
            (pattern, pattern, page_size, (page - 1) * page_size)
        ).fetchall()
    return {
        "items": [dict(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages_total": (total + page_size - 1) // page_size,
    }




@router.get("/api/items/stats")
def item_stats(q: str) -> Any:
    """Get aggregate stats for an item: total bought, avg price, suppliers, price history.

    v8.4: Includes both 'review' and 'confirmed' bills (same as search).
    """
    if not q.strip():
        return {"error": "query required"}
    with db.conn() as c:
        rows = c.execute(
            "SELECT bi.price, bi.qty, bi.unit, bi.raw, b.supplier_name, b.bill_date "
            "FROM bill_items bi JOIN bills b ON bi.bill_id = b.id "
            "WHERE b.deleted_at IS NULL AND b.status IN ('confirmed', 'review') "
            "AND (bi.raw LIKE ? OR bi.item_code LIKE ?) "
            "ORDER BY b.bill_date DESC",
            (f"%{q}%", f"%{q}%")
        ).fetchall()
    if not rows:
        return {"total_purchases": 0}
    from ..validate import pieces
    total_qty = sum(pieces(r["qty"], r["unit"]) for r in rows)
    prices = [r["price"] for r in rows if r["price"]]
    suppliers = list(set(r["supplier_name"] for r in rows if r["supplier_name"]))
    return {
        "total_purchases": len(rows),
        "total_pieces": total_qty,
        "avg_price": round(sum(prices) / len(prices), 2) if prices else 0,
        "min_price": min(prices) if prices else 0,
        "max_price": max(prices) if prices else 0,
        "suppliers": suppliers,
        "price_history": [
            {"date": r["bill_date"], "price": r["price"], "supplier": r["supplier_name"], "raw": r["raw"]}
            for r in rows[:20]
        ],
    }




@router.get("/api/bills")
def list_bills(status: str = "", q: str = "", payment: str = "",
               page: int = 1, page_size: int = PAGE_SIZE,
               sort_by: str = "", sort_order: str = "desc") -> Any:
    """List bills with search, filter, pagination, and dynamic sorting.

    v8.15.0: Added sort_by + sort_order params for dynamic column sorting.
    """
    sql = "SELECT * FROM bills WHERE deleted_at IS NULL"
    args = []
    if status:
        sql += " AND status=?"
        args.append(status)
    if payment:
        sql += " AND payment_status=?"
        args.append(payment)
    if q:
        sql += (" AND (supplier_name LIKE ? OR phone LIKE ? OR bill_no LIKE ?)")
        args += [f"%{q}%"] * 3

    # v8.15.0: Dynamic sort — validate against whitelist to prevent SQL injection
    order_clause = db.validate_sort(sort_by, sort_order, {
        "date": "COALESCE(bill_date, date(created_at))",
        "supplier": "supplier_name",
        "total": "COALESCE(written_total, computed_total)",
        "status": "status",
        "payment": "payment_status",
        "bill_no": "bill_no",
        "created": "created_at",
    }, default="COALESCE(bill_date, date(created_at)) DESC, id DESC")

    # Count for pagination
    count_sql = sql.replace("SELECT *", "SELECT COUNT(*) AS n", 1)
    with db.conn() as c:
        total = c.execute(count_sql, args).fetchone()["n"]
        # v8.19.1: serve the nearest valid page when the requested one no
        # longer exists (last-page deletion, filter shrinking the result set)
        page = db.clamp_page(page, total, page_size)
        sql += f" ORDER BY {order_clause} LIMIT ? OFFSET ?"
        args += [page_size, (page - 1) * page_size]
        rows = [dict(r) for r in c.execute(sql, args).fetchall()]

    for r in rows:
        try:
            fl = json.loads(r["flags"] or "[]")
        except Exception:
            fl = []
        # v8.18.6: sanitize — dict flags (legacy cost-overrun warnings) are
        # flattened to message strings so the UI never renders '[object Object]'
        r["flag_count"] = len(fl)
        r["flags"] = json.dumps([_flag_text(f) for f in fl])
        # v8.16.0: Count low-confidence items for each bill
        with db.conn() as c2:
            review_count = c2.execute(
                "SELECT COUNT(*) AS n FROM bill_items WHERE bill_id=? AND confidence < 0.85",
                (r["id"],)
            ).fetchone()
            r["review_count"] = review_count["n"] if review_count else 0
    pages_total = (total + page_size - 1) // page_size
    return {
        "bills": rows,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages_total": pages_total,
    }




@router.get("/api/bills/{bill_id}")
def get_bill(bill_id: int) -> Any:
    with db.conn() as c:
        bill = c.execute("SELECT * FROM bills WHERE id=? AND deleted_at IS NULL", (bill_id,)).fetchone()
        if not bill:
            raise HTTPException(404, "bill not found")
        pages = c.execute(
            "SELECT * FROM bill_pages WHERE bill_id=? ORDER BY page_no", (bill_id,)
        ).fetchall()
        items = c.execute(
            "SELECT bi.*, pc.name AS cat_name, pc.sell_price AS cat_sell_price, "
            "bi.sell_price AS ai_sell_price "
            "FROM bill_items bi LEFT JOIN price_categories pc ON bi.category_id = pc.id "
            "WHERE bi.bill_id=? ORDER BY bi.id", (bill_id,)
        ).fetchall()
    dup = detect_duplicate(
        bill["supplier_name"], bill["phone"], bill["bill_date"], exclude_id=bill_id
    ) if bill["status"] == "review" else None
    return {
        **dict(bill),
        # v8.18.6: flatten dict flags → message strings (legacy rows stored
        # cost-overrun warnings as dicts; the edit page renders each flag with
        # esc() and a dict showed up as '[object Object]')
        "flags": [_flag_text(f) for f in json.loads(bill["flags"] or "[]")],
        "pages": [dict(p) for p in pages],
        "items": [dict(i) for i in items],
        "duplicate": dup,
    }




@router.post("/api/bills/{bill_id}/sell-through-check")
def sell_through_check(bill_id: int) -> Any:
    """v8.2 Phase 5: Pre-confirm sell-through check.

    Reads the bill's items (as currently saved in review state) and computes
    sell-through for each category WITHOUT modifying any data. Returns the
    verdicts so the UI can show the soft-pause before confirming.
    """
    results = []
    with db.conn() as c:
        bill = c.execute("SELECT bill_date FROM bills WHERE id=?", (bill_id,)).fetchone()
        if not bill:
            raise HTTPException(404, "Bill not found")
        bill_date = bill["bill_date"] or datetime.now().strftime("%Y-%m-%d")
        items = c.execute(
            "SELECT bi.*, pc.code, pc.name FROM bill_items bi "
            "LEFT JOIN price_categories pc ON bi.category_id = pc.id "
            "WHERE bi.bill_id=? ORDER BY bi.id",
            (bill_id,)
        ).fetchall()
        for bi in items:
            cat_id = bi["category_id"]
            if not cat_id:
                continue
            # Find the last confirmed purchase of this category BEFORE this bill
            last_purchase = c.execute(
                "SELECT bi2.qty, bi2.unit, b2.bill_date "
                "FROM bill_items bi2 JOIN bills b2 ON bi2.bill_id=b2.id "
                "WHERE bi2.category_id=? AND b2.status='confirmed' AND b2.deleted_at IS NULL "
                "AND b2.id != ? AND b2.bill_date <= ? "
                "ORDER BY b2.bill_date DESC, b2.id DESC LIMIT 1",
                (cat_id, bill_id, bill_date)
            ).fetchone()
            if not last_purchase:
                results.append({
                    "category_id": cat_id,
                    "category_code": bi["code"],
                    "category_name": bi["name"],
                    "verdict": "first_purchase",
                    "sell_through_pct": None,
                    "last_purchase_qty": None,
                    "sold_since": None,
                })
                continue
            last_qty = float(last_purchase["qty"] or 0)
            if last_purchase["unit"] == "dozen":
                last_qty *= 12
            sold_row = c.execute(
                "SELECT COALESCE(SUM(si.qty), 0) AS v FROM sale_items si "
                "JOIN sales s ON si.sale_id=s.id "
                f"WHERE si.category_id=? AND {db.VALID_SALE_FILTER} "
                "AND date(s.created_at) >= date(?)",
                (cat_id, last_purchase["bill_date"])
            ).fetchone()
            sold_since = float(sold_row["v"] or 0)
            sell_through = (sold_since / last_qty * 100) if last_qty > 0 else 0
            if sell_through >= 80:
                verdict = "well_timed"
            elif sell_through >= 40:
                verdict = "partial"
            else:
                verdict = "overstock_risk"
            # Check if already acknowledged
            existing_ack = c.execute(
                "SELECT acknowledged FROM bill_intelligence "
                "WHERE category_id=? AND verdict='overstock_risk' AND acknowledged=1 "
                "ORDER BY id DESC LIMIT 1",
                (cat_id,)
            ).fetchone()
            is_acknowledged = bool(existing_ack)
            results.append({
                "category_id": cat_id,
                "category_code": bi["code"],
                "category_name": bi["name"],
                "verdict": verdict,
                "sell_through_pct": round(sell_through, 1),
                "last_purchase_qty": last_qty,
                "sold_since": sold_since,
                "acknowledged": is_acknowledged,
            })
    return {"bill_id": bill_id, "results": results}


@router.post("/api/bills/{bill_id}/confirm")
def confirm(bill_id: int, payload: ConfirmIn) -> Any:
    """Confirm (or re-confirm) a supplier bill — atomic.

    Phase 0 PR 5 — Atomic bill confirmation with Optimistic Concurrency Control:
    The entire confirm — old stock reversal (if re-confirming), bill_items
    replacement, supplier upsert, rate-flag check, bill status update,
    stock_state purchase application, activity_log — commits as a SINGLE
    atomic write transaction via `db.write_tx()` (BEGIN IMMEDIATE).

    v8.14.0: Refactored from ~267 LOC single function into orchestrated helpers
    for readability. Each helper handles one step and accepts the shared
    connection `c`. The atomicity guarantee is unchanged — all helpers run
    inside the same write_tx().

    Reviewers 2+3 correction — NO `confirm_lock` column:
    Previous plan proposed a `bills.confirm_lock TEXT` UUID column to prevent
    concurrent double-clicks. Reviewers correctly pointed out that if the
    Python process crashed while holding the lock, the database would be left
    with a stale lock forever — permanently blocking future confirms.

    The fix uses SQLite's natural locking + OCC via the `bills.version` column:
      1. Request A enters write_tx() → BEGIN IMMEDIATE → acquires write lock
      2. Request B enters write_tx() → BEGIN IMMEDIATE → BLOCKS up to 5s
      3. Request A commits (version 1 → 2)
      4. Request B unblocks, acquires lock, re-reads bill, sees version=2
         (mismatch with the version=1 it expected) → returns 409

    Re-confirm logic (v8.5.5 fix preserved):
    When re-confirming an already-confirmed bill, the OLD bill_items are
    reversed via `reverse_purchase_in_state()` (using ORIGINAL price, NOT
    current avg_cost — the v8.5.5 double-subtraction bug fix). Then the NEW
    bill_items are applied via `apply_purchase_to_state()`. Both happen inside
    the same write_tx() so a mid-reversal failure cannot leave the stock
    state corrupted.

    `rebuild_stock_state()` is moved OUTSIDE the txn (post-commit) because
    it's O(n) and would hold the lock too long.
    """
    # ─── Atomic write transaction ───────────────────────────────────────────
    with db.write_tx() as c:
        # (1) SELECT bill + (2) OCC guard
        bill_row, was_confirmed, expected_version = _confirm_check_and_increment(c, bill_id)

        # (3) Capture OLD bill_items for reversal + (4) corrections audit
        old_items = _confirm_capture_old_items(c, bill_id)
        _confirm_insert_corrections(c, bill_id, old_items, payload.items)

        # (5) DELETE old bill_items + INSERT new ones
        computed, final_unit, default_cat_id = _confirm_replace_bill_items(c, bill_id, payload.items)

        # (6) Upsert supplier inline
        sup_id = _confirm_upsert_supplier(c, payload)

        # (7) Rate flags + (7b) cost-vs-cheapest-supplier warnings
        rate_flags = _confirm_check_rates_and_costs(c, bill_id, payload.items, sup_id)

        # (8) Merge flags + (9) UPDATE bills SET status='confirmed'
        _confirm_update_bill_status(c, bill_id, payload, sup_id, computed, final_unit, rate_flags)

        # (10) Reverse OLD purchases (if re-confirming)
        if was_confirmed:
            _confirm_reverse_old_purchases(c, bill_id, old_items, payload.bill_date)

        # (11) Apply NEW purchases
        _confirm_apply_new_purchases(c, bill_id, payload.items, default_cat_id, payload.bill_date)

        # (12) Activity log — bill_confirmed
        db.log_activity(
            "bill_confirmed", "bill", bill_id,
            f"Confirmed bill #{bill_id} from {payload.supplier_name or 'Unknown'} "
            f"({len(payload.items)} items, Rs {computed:.0f})",
            {"supplier": payload.supplier_name, "items": len(payload.items),
             "total": computed, "payment": payload.payment_status,
             "was_reconfirmed": was_confirmed,
             "new_version": expected_version + 1},
            c=c,
        )

    # ─── Post-commit (outside txn) ──────────────────────────────────────────
    intel = _confirm_post_commit_intelligence(bill_id, was_confirmed)
    return {"ok": True, "bill_intelligence": intel, "new_version": expected_version + 1}


# ─── v8.14.0: confirm() helpers — each handles one step, accepts shared connection c ──

def _confirm_check_and_increment(c, bill_id: int) -> tuple:
    """Step 1+2: SELECT bill + OCC version increment. Returns (bill_row, was_confirmed, expected_version).

    v8.18.15: soft-deleted bills are rejected here — confirming a deleted
    bill would apply stock for a bill every report/rebuild excludes, silently
    inflating category_stock_state.
    """
    bill_row = c.execute(
        "SELECT status, version, deleted_at FROM bills WHERE id=?", (bill_id,)
    ).fetchone()
    if not bill_row:
        raise HTTPException(404, "bill not found")
    if bill_row["deleted_at"] is not None:
        raise HTTPException(409, {
            "error": "bill_deleted",
            "message": "This bill was deleted. Restore it before confirming.",
            "bill_id": bill_id,
        })
    was_confirmed = (bill_row["status"] == 'confirmed')
    expected_version = bill_row["version"]
    cur = c.execute(
        "UPDATE bills SET version=version+1 WHERE id=? AND version=?",
        (bill_id, expected_version),
    )
    if cur.rowcount == 0:
        raise HTTPException(409, {
            "error": "bill_version_mismatch",
            "message": "Bill was modified by another request. Please reload and try again.",
            "bill_id": bill_id,
            "expected_version": expected_version,
        })
    return (bill_row, was_confirmed, expected_version)


def _confirm_capture_old_items(c, bill_id: int) -> list:
    """Step 3: Capture OLD bill_items for reversal (if re-confirming)."""
    return c.execute(
        "SELECT bi.id, bi.category_id, bi.qty, bi.unit, bi.price, bi.raw "
        "FROM bill_items bi WHERE bi.bill_id=? ORDER BY bi.id",
        (bill_id,),
    ).fetchall()


def _confirm_insert_corrections(c, bill_id: int, old_items: list, new_items: list) -> None:
    """Step 4: Insert corrections rows for any field diffs (audit trail)."""
    if len(old_items) != len(new_items):
        c.execute(
            "INSERT INTO corrections(bill_id, field, before, after) VALUES(?,?,?,?)",
            (bill_id, "item_count", str(len(old_items)), str(len(new_items))),
        )
    for o, n in zip(old_items, new_items):
        for field in ("price", "qty", "unit", "raw", "category_id"):
            old_val = str(o[field]) if o[field] is not None else ""
            new_val = str(getattr(n, field)) if getattr(n, field) is not None else ""
            if old_val != new_val:
                c.execute(
                    "INSERT INTO corrections(bill_id, field, before, after) VALUES(?,?,?,?)",
                    (bill_id, f"item {o['id']} {field}", old_val, new_val),
                )


def _confirm_replace_bill_items(c, bill_id: int, items: list) -> tuple:
    """Step 5: DELETE old bill_items + INSERT new ones. Returns (computed, final_unit, default_cat_id)."""
    c.execute("DELETE FROM bill_items WHERE bill_id=?", (bill_id,))
    default_cat = c.execute(
        "SELECT id FROM price_categories WHERE sell_price=250 AND active=1 LIMIT 1"
    ).fetchone()
    default_cat_id = default_cat["id"] if default_cat else None
    computed = 0
    final_unit = "pcs"
    for n in items:
        lt = n.price * pieces(n.qty, n.unit)
        computed += lt
        final_unit = n.unit
        cat_id = n.category_id if n.category_id else default_cat_id
        c.execute(
            "INSERT INTO bill_items(bill_id, raw, item_code, price, qty, unit, line_total, "
            "category_id, corrected, page_no) VALUES(?,?,?,?,?,?,?,?,'1',?)",
            (bill_id, n.raw, n.item_code or None, n.price, n.qty, n.unit, lt, cat_id, n.page_no),
        )
    return (computed, final_unit, default_cat_id)


def _confirm_upsert_supplier(c, payload) -> int:
    """Step 6: Upsert supplier inline (don't call shop.upsert_supplier — own conn)."""
    sup_id = None
    if payload.supplier_name or payload.phone:
        row = None
        if payload.phone:
            row = c.execute(
                "SELECT id FROM suppliers WHERE phone=?", (payload.phone,)
            ).fetchone()
        if not row and payload.supplier_name:
            row = c.execute(
                "SELECT id FROM suppliers WHERE lower(name)=lower(?)",
                (payload.supplier_name,),
            ).fetchone()
        if row:
            sup_id = row["id"]
            c.execute(
                "UPDATE suppliers SET name=COALESCE(NULLIF(?,''),name), "
                "phone=COALESCE(NULLIF(?,''),phone) WHERE id=?",
                (payload.supplier_name, payload.phone, sup_id),
            )
        else:
            sup_id = c.execute(
                "INSERT INTO suppliers(name, phone, notes) VALUES(?,?,?)",
                (payload.supplier_name or "Unknown", payload.phone, payload.notes),
            ).lastrowid
    return sup_id


def _confirm_check_rates_and_costs(c, bill_id: int, items: list, sup_id: int) -> list:
    """Step 7+7b: Rate flag check + cost-vs-cheapest-supplier warnings."""
    rate_flags = []
    if sup_id:
        rate_flags = shop_mod.check_bill_items_against_rates(
            [n.dict() if hasattr(n, 'dict') else n for n in items],
            sup_id, c=c,
        )
    try:
        from ..category_ops import check_bill_cost_vs_cheapest_supplier
        cost_warnings = check_bill_cost_vs_cheapest_supplier(
            [n.dict() if hasattr(n, 'dict') else n for n in items]
        )
        for w in cost_warnings:
            # v8.18.6: store the human-readable message string, NOT the dict.
            # Dict flags render as '[object Object]' in the edit-bill alerts.
            rate_flags.append(w["message"])
    except Exception as _e:
        from .. import profit as _profit_mod
        if hasattr(_profit_mod, 'log_state_drift'):
            _profit_mod.log_state_drift('check_bill_cost_vs_cheapest_supplier', bill_id, str(_e), {})
    return rate_flags


def _confirm_update_bill_status(c, bill_id: int, payload, sup_id: int,
                                 computed: float, final_unit: str, rate_flags: list) -> None:
    """Step 8+9: Merge flags + UPDATE bills SET status='confirmed'."""
    existing_flags = []
    try:
        existing_flags = json.loads(payload.flags or "[]")
    except Exception:
        existing_flags = []
    all_flags = [_flag_text(f) for f in (existing_flags + rate_flags)]
    c.execute(
        "UPDATE bills SET supplier_id=?, supplier_name=?, phone=?, bill_date=?, bill_no=?, "
        "written_total=?, computed_total=?, unit=?, status='confirmed', "
        "payment_status=?, credit_due_date=?, flags=? WHERE id=?",
        (sup_id, payload.supplier_name, payload.phone, payload.bill_date, payload.bill_no,
         payload.written_total, computed, final_unit,
         payload.payment_status, payload.credit_due_date, json.dumps(all_flags), bill_id),
    )


def _confirm_reverse_old_purchases(c, bill_id: int, old_items: list, bill_date: str) -> None:
    """Step 10: Reverse OLD purchases (if re-confirming) — uses ORIGINAL price."""
    for oi in old_items:
        if oi["category_id"] and oi["price"] and oi["price"] > 0:
            old_qty = pieces(oi["qty"], oi["unit"])
            old_price = float(oi["price"])
            try:
                profit_mod.reverse_purchase_in_state(
                    oi["category_id"], old_qty, old_price,
                    txn_at=bill_date, c=c,
                )
            except Exception as e:
                profit_mod.log_state_drift(
                    "reverse_purchase_in_state", oi["category_id"], str(e),
                    {"bill_id": bill_id, "qty": old_qty, "unit_price": old_price},
                    c=c,
                )


def _confirm_apply_new_purchases(c, bill_id: int, items: list, default_cat_id: int, bill_date: str) -> None:
    """Step 11: Apply NEW purchases via shared connection."""
    for n in items:
        cat_id = n.category_id if n.category_id else default_cat_id
        if cat_id and n.price and n.price > 0 and n.qty and n.qty > 0:
            try:
                profit_mod.apply_purchase_to_state(
                    cat_id, pieces(n.qty, n.unit), float(n.price),
                    txn_at=bill_date, c=c,
                )
            except Exception as e:
                profit_mod.log_state_drift(
                    "apply_purchase_to_state", cat_id, str(e),
                    {"bill_id": bill_id, "qty": pieces(n.qty, n.unit),
                     "unit_price": float(n.price)},
                    c=c,
                )


def _confirm_post_commit_intelligence(bill_id: int, was_confirmed: bool) -> list:
    """Post-commit: compute bill intelligence + rebuild stock state (if re-confirming)."""
    try:
        from ..bill_intel import compute_bill_intelligence
        intel = compute_bill_intelligence(bill_id)
    except Exception as e:
        logger.error(f"Bill intelligence failed for bill {bill_id}: {e}")
        intel = []
    if was_confirmed:
        try:
            profit_mod.rebuild_stock_state()
        except Exception as e:
            logger.warning(f"rebuild_stock_state after re-confirm failed: {e}")
    return intel









@router.delete("/api/bills/{bill_id}")
def delete_bill(bill_id: int, permanent: bool = False) -> Any:
    """Soft-delete bill (mark deleted_at). Use permanent=true for hard delete.

    Soft-deleted bills are excluded from list/get queries but can be restored
    within a 5-minute window via the undo toast in the UI.

    v8.18.15: deleting a CONFIRMED bill now REVERSES its stock effect
    (category_stock_state) inside the same atomic transaction, so the
    inventory page and every stock-derived number reflect the deletion
    immediately — no restart / rebuild needed. Previously the running state
    kept the deleted bill's qty+value until the next full rebuild_stock_state
    (which only ran at boot), which is exactly the "changes don't show until
    I restart the app" bug. restore_bill() re-applies the purchase, keeping
    the 5-minute undo perfectly symmetric.
    """
    sup_name = ""
    reversed_stock_lines = 0
    with db.write_tx() as c:
        row = c.execute(
            "SELECT supplier_name, status, deleted_at FROM bills WHERE id=?",
            (bill_id,),
        ).fetchone()
        if not row:
            raise HTTPException(404, "bill not found")
        sup_name = row["supplier_name"] or "Unknown"
        already_deleted = row["deleted_at"] is not None
        # Reverse stock exactly ONCE — only when a live (not-yet-deleted)
        # CONFIRMED bill transitions to deleted. Hard-deleting an already
        # soft-deleted bill must NOT reverse again (it was reversed at
        # soft-delete time). Review bills never touched stock.
        if not already_deleted and row["status"] == "confirmed":
            reversed_stock_lines = _reverse_bill_stock(c, bill_id)
        if permanent:
            pages = c.execute(
                "SELECT filename FROM bill_pages WHERE bill_id=?", (bill_id,)
            ).fetchall()
            # bill_items removed via FK ON DELETE CASCADE (foreign_keys=ON)
            c.execute("DELETE FROM bills WHERE id=?", (bill_id,))
            for p in pages:
                try:
                    (PAGES / p["filename"]).unlink(missing_ok=True)
                except Exception as _e:
                    logger.warning("Silent exception in bills.py: %s", _e, exc_info=True)
        elif not already_deleted:
            from datetime import datetime
            c.execute(
                "UPDATE bills SET deleted_at=? WHERE id=?",
                (datetime.now().isoformat(), bill_id),
            )
        else:
            # Idempotent: soft-deleting an already soft-deleted bill is a no-op
            return {"ok": True, "soft_deleted": True, "idempotent": True,
                    "reversed_stock_lines": 0}
    db.log_activity(
        "bill_deleted", "bill", bill_id,
        f"Deleted bill #{bill_id} ({sup_name})" + (" permanently" if permanent else ""),
        {"supplier": sup_name, "permanent": permanent,
         "reversed_stock_lines": reversed_stock_lines},
    )
    return {"ok": True, "soft_deleted": not permanent,
            "reversed_stock_lines": reversed_stock_lines}


def _reverse_bill_stock(c, bill_id: int) -> int:
    """v8.18.15: reverse a confirmed bill's stock contribution.

    Mirrors _confirm_reverse_old_purchases: uses each bill_item's ORIGINAL
    price (NOT current avg cost) and pieces-qty (dozen → ×12), skips lines
    without category/price/qty, and logs state drift instead of failing the
    delete. Must run inside the caller's write_tx — never commits itself.
    """
    items = c.execute(
        "SELECT category_id, price, qty, unit FROM bill_items WHERE bill_id=?",
        (bill_id,),
    ).fetchall()
    reversed_lines = 0
    for it in items:
        if it["category_id"] and it["price"] and it["price"] > 0 \
                and it["qty"] and it["qty"] > 0:
            qty = pieces(it["qty"], it["unit"])
            unit_price = float(it["price"])
            try:
                profit_mod.reverse_purchase_in_state(
                    it["category_id"], qty, unit_price, c=c,
                )
                reversed_lines += 1
            except Exception as e:
                profit_mod.log_state_drift(
                    "reverse_purchase_in_state", it["category_id"], str(e),
                    {"bill_id": bill_id, "qty": qty, "unit_price": unit_price},
                    c=c,
                )
    return reversed_lines


def _reapply_bill_stock(c, bill_id: int) -> int:
    """v8.18.15: re-apply a confirmed bill's stock contribution (undo path).

    Exact mirror of _reverse_bill_stock: same items, same ORIGINAL prices —
    so soft-delete → restore returns the running state to exactly what it
    was. Must run inside the caller's write_tx — never commits itself.
    """
    items = c.execute(
        "SELECT category_id, price, qty, unit FROM bill_items WHERE bill_id=?",
        (bill_id,),
    ).fetchall()
    applied_lines = 0
    for it in items:
        if it["category_id"] and it["price"] and it["price"] > 0 \
                and it["qty"] and it["qty"] > 0:
            qty = pieces(it["qty"], it["unit"])
            unit_price = float(it["price"])
            try:
                profit_mod.apply_purchase_to_state(
                    it["category_id"], qty, unit_price, c=c,
                )
                applied_lines += 1
            except Exception as e:
                profit_mod.log_state_drift(
                    "apply_purchase_in_restore", it["category_id"], str(e),
                    {"bill_id": bill_id, "qty": qty, "unit_price": unit_price},
                    c=c,
                )
    return applied_lines




@router.post("/api/bills/{bill_id}/restore")
def restore_bill(bill_id: int) -> Any:
    """Restore a soft-deleted bill (undo).

    v8.18.15: restoring a CONFIRMED bill re-applies its stock effect —
    symmetric with delete_bill()'s reversal — so the undo toast leaves
    zero side effects on category_stock_state.
    """
    with db.write_tx() as c:
        row = c.execute(
            "SELECT status, deleted_at, supplier_name FROM bills WHERE id=?",
            (bill_id,),
        ).fetchone()
        if not row:
            raise HTTPException(404, "bill not found")
        if not row["deleted_at"]:
            # Idempotent: restoring a live bill is a no-op
            return {"ok": True, "idempotent": True, "reapplied_stock_lines": 0}
        reapplied_stock_lines = 0
        if row["status"] == "confirmed":
            reapplied_stock_lines = _reapply_bill_stock(c, bill_id)
        c.execute("UPDATE bills SET deleted_at=NULL WHERE id=?", (bill_id,))
        sup_name = row["supplier_name"] or "Unknown"
    db.log_activity(
        "bill_restored", "bill", bill_id,
        f"Restored bill #{bill_id} ({sup_name})",
        {"supplier": sup_name, "reapplied_stock_lines": reapplied_stock_lines},
    )
    return {"ok": True, "reapplied_stock_lines": reapplied_stock_lines}




@router.patch("/api/bills/{bill_id}")
def patch_bill(bill_id: int, payload: PatchBill) -> Any:
    """Inline update of bill fields without rewriting items."""
    fields = []
    args = []
    for f in ("supplier_name", "phone", "bill_date", "bill_no",
              "written_total", "payment_status", "credit_due_date"):
        v = getattr(payload, f)
        if v is not None:
            fields.append(f"{f}=?")
            args.append(v)
    if not fields:
        return {"ok": False, "updated": 0}
    args.append(bill_id)
    with db.conn() as c:
        cur = c.execute(
            f"UPDATE bills SET {', '.join(fields)} WHERE id=?", args
        )
        updated = cur.rowcount
    return {"ok": True, "updated": updated}




@router.post("/api/bills/empty")
def empty_bill() -> Any:
    """Create a blank bill for manual entry."""
    with db.conn() as c:
        bill_id = c.execute(
            "INSERT INTO bills(status) VALUES('review')"
        ).lastrowid
    return {"id": bill_id}


# ------------------------------------------------------------------
# Suppliers
# ------------------------------------------------------------------



@router.get("/api/bills/{bill_id}/whatsapp")
def bill_whatsapp_link(bill_id: int) -> Any:
    """Generate WhatsApp link for a single overdue bill reminder."""
    with db.conn() as c:
        b = c.execute(
            "SELECT id, supplier_id, supplier_name, phone, written_total, computed_total, "
            "bill_date, credit_due_date FROM bills WHERE id=? AND deleted_at IS NULL",
            (bill_id,)
        ).fetchone()
        if not b:
            raise HTTPException(404, "bill not found")
    phone = b["phone"] or ""
    phone_clean = re.sub(r"[\s\-+]", "", phone)
    if phone_clean.startswith("03"):
        phone_clean = "92" + phone_clean[1:]
    amt = b["written_total"] or b["computed_total"] or 0
    due = b["credit_due_date"][:10] if b["credit_due_date"] else "—"
    msg = (
        f"Assalam o Alaikum {b['supplier_name']},\n\n"
        f"Reminder: Bill #{b['id']} dated {b['bill_date'][:10] if b['bill_date'] else '—'} "
        f"for Rs {amt:.0f} is pending (due {due}).\n\n"
        f"Please arrange payment. JazakAllah."
    )
    url = f"https://wa.me/{phone_clean}?text={quote(msg)}" if phone_clean else None
    return {"message": msg, "url": url, "phone": phone_clean}


# ------------------------------------------------------------------
# Export
# ------------------------------------------------------------------



@router.get("/api/providers")
def list_providers() -> Any:
    with db.conn() as c:
        rows = c.execute(
            "SELECT id, name, provider_type, model, priority, enabled, api_key FROM ai_providers "
            "ORDER BY priority, id"
        ).fetchall()
    # Mask API keys: decrypt for preview, then mask
    result = []
    for r in rows:
        d = dict(r)
        decrypted_key = crypto_mod.decrypt_api_key(d.pop("api_key", ""))
        d["key_preview"] = crypto_mod.mask_api_key(decrypted_key)
        result.append(d)
    return result




@router.post("/api/providers")
def add_provider(payload: ProviderIn) -> Any:
    encrypted_key = crypto_mod.encrypt_api_key(payload.api_key)
    with db.conn() as c:
        pid = c.execute(
            "INSERT INTO ai_providers(name, provider_type, api_key, model, priority, enabled) "
            "VALUES(?,?,?,?,?,?)",
            (payload.name, payload.provider_type, encrypted_key, payload.model,
             payload.priority, 1 if payload.enabled else 0),
        ).lastrowid
    return {"id": pid}




@router.put("/api/providers/{pid}")
def update_provider(pid: int, payload: ProviderIn) -> Any:
    encrypted_key = crypto_mod.encrypt_api_key(payload.api_key)
    with db.conn() as c:
        c.execute(
            "UPDATE ai_providers SET name=?, provider_type=?, api_key=?, model=?, "
            "priority=?, enabled=? WHERE id=?",
            (payload.name, payload.provider_type, encrypted_key, payload.model,
             payload.priority, 1 if payload.enabled else 0, pid),
        )
    return {"ok": True}




@router.delete("/api/providers/{pid}")
def delete_provider(pid: int) -> Any:
    with db.conn() as c:
        c.execute("DELETE FROM ai_providers WHERE id=?", (pid,))
    return {"ok": True}




@router.post("/api/providers/test")
def test_provider_route(payload: TestProviderIn) -> Any:
    """Test a provider's API key + model without saving. Used by the add/edit modal."""
    try:
        result = extract.test_provider(payload.provider_type, payload.api_key, payload.model)
        return result
    except Exception as e:
        # v8.13.3: Was status_code=200 — clients couldn't distinguish success from failure.
        return JSONResponse({"ok": False, "error": str(e)}, status_code=502)




@router.post("/api/providers/{pid}/test")
def test_existing_provider(pid: int) -> Any:
    """Test an already-saved provider by its ID."""
    with db.conn() as c:
        row = c.execute("SELECT * FROM ai_providers WHERE id=?", (pid,)).fetchone()
    if not row:
        raise HTTPException(404, "provider not found")
    # Decrypt the API key before testing
    decrypted_key = crypto_mod.decrypt_api_key(row["api_key"])
    # v8.18.4 FIX: decrypt_api_key silently returns the ciphertext when
    # decryption fails (e.g. after a DB restore or password reset — the
    # Fernet key is derived from password_hash). Sending that ciphertext
    # to the provider is EXACTLY why users see 400/401 "invalid key"
    # errors even though their API keys are perfectly valid. Detect it
    # here and tell the user what to do instead of leaking it onward.
    if decrypted_key.startswith("gAAAAA"):
        return JSONResponse(
            {"ok": False,
             "error": "Stored key cannot be decrypted (password was reset or DB "
                      "restored from another install). Click Edit, re-enter the "
                      "same API key, and Save — that re-encrypts it correctly. "
                      "This silent bad-key is why AI features return 400/401."},
            status_code=502,
        )
    try:
        result = extract.test_provider(row["provider_type"], decrypted_key, row["model"] or "")
        return result
    except Exception as e:
        # v8.13.3: Was status_code=200 — clients couldn't distinguish success from failure.
        return JSONResponse({"ok": False, "error": str(e)}, status_code=502)


# ------------------------------------------------------------------
# Accuracy
# ------------------------------------------------------------------



@router.get("/api/accuracy")
def accuracy() -> Any:
    with db.conn() as c:
        items = c.execute(
            "SELECT COUNT(*) n FROM bill_items bi "
            "JOIN bills b ON bi.bill_id = b.id "
            "WHERE b.status='confirmed' AND b.deleted_at IS NULL"
        ).fetchone()["n"]
        corr = c.execute(
            "SELECT COUNT(*) n FROM corrections WHERE field NOT IN ('item_count')"
        ).fetchone()["n"]
    fields = items * 5  # raw, price, qty, unit, category_id
    if not fields:
        return {"fields": 0, "corrected": 0, "accuracy": None}
    acc = max(0.0, round(1 - corr / fields, 2))
    return {"fields": fields, "corrected": corr, "accuracy": acc}


# ------------------------------------------------------------------
# Activity feed
# ------------------------------------------------------------------



@router.get("/api/activity")
def list_activity(limit: int = 20, event_type: str = "", entity_type: str = "",
                  start: str = "", end: str = "", page: int = 0, page_size: int = 0) -> Any:
    """Recent activity events for the dashboard feed.

    v4.0 Phase 4: added filters — event_type, entity_type, start/end (YYYY-MM-DD).
    v8.4: added pagination — page & page_size params.
    Backward-compatible: all filters optional, returns plain list when no pagination.
    """
    # Clamp limit
    limit = min(max(1, limit), 500)
    use_pagination = page > 0 or page_size > 0
    if use_pagination:
        page = max(1, page)
        page_size = min(max(1, page_size or limit), 500)
        offset = (page - 1) * page_size
    else:
        page_size = limit
        offset = 0

    sql = "SELECT * FROM activity_log WHERE 1=1"
    args = []
    if event_type:
        sql += " AND event_type=?"
        args.append(event_type)
    if entity_type:
        sql += " AND entity_type=?"
        args.append(entity_type)
    if start:
        sql += " AND date(created_at)>=?"
        args.append(start)
    if end:
        sql += " AND date(created_at)<=?"
        args.append(end)

    # Count total for pagination
    count_sql = sql.replace("SELECT *", "SELECT COUNT(*) AS n", 1)

    with db.conn() as c:
        total = c.execute(count_sql, args).fetchone()["n"]
        # v8.19.1: clamp the page BEFORE baking the OFFSET into the query
        if use_pagination:
            page = db.clamp_page(page, total, page_size)
            offset = (page - 1) * page_size
        # v8.19.1 fix (pre-existing bug): the ORDER BY referenced bill_date,
        # which does not exist on activity_log — every paginated /api/activity
        # call failed with "no such column: bill_date".
        sql += " ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?"
        args += [page_size, offset]
        rows = c.execute(sql, args).fetchall()

    import json as _json
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["metadata"] = _json.loads(r["metadata"] or "{}")
        except Exception:
            d["metadata"] = {}
        out.append(d)

    if use_pagination:
        pages_total = (total + page_size - 1) // page_size
        return {
            "activity": out,
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages_total": pages_total,
        }
    return {"activity": out}


@router.get("/api/activity/export")
def export_activity_csv(event_type: str = "", entity_type: str = "",
                        start: str = "", end: str = "") -> Any:
    """Export activity log as CSV (v4.0 Phase 4)."""
    import csv, io
    sql = "SELECT id, event_type, entity_type, entity_id, description, metadata, created_at FROM activity_log WHERE 1=1"
    args = []
    if event_type:
        sql += " AND event_type=?"; args.append(event_type)
    if entity_type:
        sql += " AND entity_type=?"; args.append(entity_type)
    if start:
        sql += " AND date(created_at)>=?"; args.append(start)
    if end:
        sql += " AND date(created_at)<=?"; args.append(end)
    sql += " ORDER BY created_at DESC, id DESC LIMIT 10000"
    with db.conn() as c:
        rows = c.execute(sql, args).fetchall()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "event_type", "entity_type", "entity_id", "description", "metadata", "created_at"])
    for r in rows:
        w.writerow([r["id"], r["event_type"], r["entity_type"], r["entity_id"],
                    r["description"], r["metadata"], r["created_at"]])
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=activity_log.csv"},
    )


# ------------------------------------------------------------------
# Bill duplicate (template)
# ------------------------------------------------------------------



@router.post("/api/bills/{bill_id}/duplicate")
def duplicate_bill(bill_id: int) -> Any:
    """Clone an existing bill as a template — copies supplier, items, but marks as new review."""
    with db.conn() as c:
        src = c.execute("SELECT * FROM bills WHERE id=? AND deleted_at IS NULL", (bill_id,)).fetchone()
        if not src:
            raise HTTPException(404, "source bill not found")
        # Create new bill row
        new_id = c.execute(
            "INSERT INTO bills(supplier_id, supplier_name, phone, unit, status, provider) "
            "VALUES(?,?,?,?,?,?)",
            (src["supplier_id"], src["supplier_name"], src["phone"], src["unit"],
             "review", "duplicated"),
        ).lastrowid
        # Copy items (without bill_no, written_total, dates — user must fill in)
        items = c.execute(
            "SELECT raw, item_code, price, qty, unit, line_total, category_id FROM bill_items WHERE bill_id=?",
            (bill_id,),
        ).fetchall()
        for it in items:
            c.execute(
                "INSERT INTO bill_items(bill_id, raw, item_code, price, qty, unit, line_total, category_id) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (new_id, it["raw"], it["item_code"], it["price"], it["qty"],
                 it["unit"], it["line_total"], it["category_id"]),
            )
    db.log_activity(
        "bill_created", "bill", new_id,
        f"Duplicated bill #{bill_id} → #{new_id} as template",
        {"source_bill_id": bill_id, "supplier": src["supplier_name"] or "Unknown"},
    )
    return {"id": new_id}


# ------------------------------------------------------------------
# Backup
# ------------------------------------------------------------------



@router.post("/api/backup")
def backup_now() -> Any:
    """Create a timestamped backup of the SQLite DB + uploads."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = BACKUPS / ts
    backup_dir.mkdir(parents=True, exist_ok=True)
    # Copy DB
    import shutil
    shutil.copy2(db.DB_PATH, backup_dir / "billbook.db")
    # Copy uploads + pages
    for src_dir in (UPLOADS, PAGES):
        if src_dir.exists():
            dst = backup_dir / src_dir.name
            shutil.copytree(src_dir, dst, dirs_exist_ok=True)
    # Keep only last 10 backups
    backups = sorted(BACKUPS.iterdir(), reverse=True)
    for old in backups[10:]:
        shutil.rmtree(old, ignore_errors=True)
    size_mb = round(
        sum(f.stat().st_size for f in backup_dir.rglob("*") if f.is_file()) / 1e6, 2)
    db.log_activity(
        "backup_created", "backup", None,
        f"Created backup ({size_mb} MB)",
        {"size_mb": size_mb},
    )
    return {"ok": True, "path": str(backup_dir), "size_mb": size_mb}




@router.get("/api/backups")
def list_backups() -> Any:
    if not BACKUPS.exists():
        return {"backups": []}
    out = []
    for d in sorted(BACKUPS.iterdir(), reverse=True):
        if d.is_dir():
            size = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
            out.append({"name": d.name, "size_mb": round(size / 1e6, 2)})
    return {"backups": out[:20]}


# ------------------------------------------------------------------
# Payment Methods (user-configurable: cash, card, online, etc.)
# ------------------------------------------------------------------



@router.get("/api/payment-methods")
def list_payment_methods() -> Any:
    return shop_mod.get_payment_methods()




@router.post("/api/payment-methods")
def add_payment_method(payload: PaymentMethodIn) -> Any:
    with db.conn() as c:
        pid = c.execute(
            "INSERT INTO payment_methods(name, type, icon, sort_order) VALUES(?,?,?,?)",
            (payload.name, payload.type, payload.icon, payload.sort_order),
        ).lastrowid
    return {"id": pid}




@router.delete("/api/payment-methods/{pid}")
def delete_payment_method(pid: int) -> Any:
    with db.conn() as c:
        c.execute("UPDATE payment_methods SET active=0 WHERE id=?", (pid,))
    return {"ok": True}


# ------------------------------------------------------------------
# Inventory (computed: purchased - sold)
# ------------------------------------------------------------------



@router.get("/api/backup/verify")
def verify_backup_integrity() -> Any:
    """Verify database integrity by running PRAGMA integrity_check on a temp copy."""
    import shutil, tempfile, sqlite3
    from ..config import DATA
    src = str(DATA / "billbook.db")
    # Copy to temp
    tmp = tempfile.mktemp(suffix=".db")
    shutil.copy2(src, tmp)
    try:
        conn = sqlite3.connect(tmp)
        result = conn.execute("PRAGMA integrity_check").fetchone()
        conn.close()
        integrity = result[0] if result else "unknown"
        # Also check quick_integrity
        conn2 = sqlite3.connect(tmp)
        q = conn2.execute("PRAGMA quick_check").fetchone()
        conn2.close()
        quick = q[0] if q else "unknown"
        return {"integrity_check": integrity, "quick_check": quick, "ok": integrity == "ok"}
    finally:
        os.unlink(tmp)


# ════════════════════════════════════════════════════════════════════════════════
# v8.16.6: Backup Import / Restore / Download
# ════════════════════════════════════════════════════════════════════════════════

def _verify_sqlite_db(path: str) -> tuple[bool, str]:
    """Verify a file is a valid SQLite DB. Returns (ok, message)."""
    import sqlite3, os
    if not os.path.exists(path):
        return False, "File not found"
    if os.path.getsize(path) < 100:
        return False, "File too small to be a valid database"
    try:
        conn = sqlite3.connect(path)
        cur = conn.cursor()
        # Check it's a SQLite DB by reading sqlite_master
        cur.execute("SELECT COUNT(*) FROM sqlite_master")
        n = cur.fetchone()[0]
        # Run integrity check
        cur.execute("PRAGMA integrity_check")
        integrity = cur.fetchone()[0]
        conn.close()
        if integrity != "ok":
            return False, f"Integrity check failed: {integrity}"
        return True, f"Valid SQLite DB ({n} objects in sqlite_master)"
    except sqlite3.DatabaseError as e:
        return False, f"Database error: {e}"
    except Exception as e:
        return False, f"Error: {e}"


@router.post("/api/backup/upload")
def upload_backup(file: UploadFile = File(...)) -> Any:
    """Upload a backup file (.db or .zip) and save it to data/backups/upload_<timestamp>/.

    For .db files: saved directly as billbook.db inside a new backup directory.
    For .zip files: extracted into a new backup directory (expects billbook.db inside).

    Returns the backup name (directory name) so the UI can offer to restore it.
    Does NOT auto-restore — user must click "Restore" separately.
    """
    import shutil, tempfile, zipfile
    from ..config import DATA
    BACKUPS.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_name = f"upload_{ts}"
    backup_dir = BACKUPS / backup_name
    # Avoid collision
    counter = 0
    while backup_dir.exists():
        counter += 1
        backup_name = f"upload_{ts}_{counter}"
        backup_dir = BACKUPS / backup_name
    backup_dir.mkdir(parents=True)

    # Read uploaded file
    # H1 fix (v8.13.4): never trust the user-supplied filename. Use a
    # server-generated name (billbook.db for .db uploads, upload.bin for
    # everything else) and validate that the resolved path is inside
    # backup_dir (defense against traversal via crafted filenames).
    import secrets as _secrets
    suffix = Path(file.filename or "unknown").suffix.lower()
    safe_name = "billbook.db" if suffix == ".db" else f"upload_{_secrets.token_hex(8)}.bin"
    tmp_file = (backup_dir / safe_name).resolve()
    try:
        tmp_file.relative_to(backup_dir.resolve())
    except ValueError:
        shutil.rmtree(backup_dir, ignore_errors=True)
        raise HTTPException(400, "Resolved upload path escapes backup dir")
    try:
        with tmp_file.open("wb") as f:
            while True:
                chunk = file.file.read(1024 * 1024)  # 1MB chunks
                if not chunk:
                    break
                f.write(chunk)
    except Exception as e:
        shutil.rmtree(backup_dir, ignore_errors=True)
        raise HTTPException(500, f"Upload failed: {e}")

    # Handle .zip — extract
    if suffix == ".zip" or tmp_file.suffix.lower() == ".zip":
        try:
            with zipfile.ZipFile(tmp_file, "r") as zf:
                zf.extractall(backup_dir)
            tmp_file.unlink()  # remove the zip itself
        except zipfile.BadZipFile:
            shutil.rmtree(backup_dir, ignore_errors=True)
            raise HTTPException(400, "Invalid ZIP file")
        # Find the .db inside
        db_files = list(backup_dir.rglob("*.db"))
        if not db_files:
            shutil.rmtree(backup_dir, ignore_errors=True)
            raise HTTPException(400, "ZIP must contain a .db file")
        # Move it to backup_dir/billbook.db
        target = backup_dir / "billbook.db"
        if db_files[0] != target:
            if target.exists():
                target.unlink()
            shutil.move(str(db_files[0]), str(target))
        # Clean up any subdirs created during extraction
        for sub in backup_dir.iterdir():
            if sub.is_dir():
                shutil.rmtree(sub, ignore_errors=True)

    # Verify the resulting billbook.db
    db_path = backup_dir / "billbook.db"
    if not db_path.exists():
        shutil.rmtree(backup_dir, ignore_errors=True)
        raise HTTPException(400, "No billbook.db found in upload")

    ok, msg = _verify_sqlite_db(str(db_path))
    if not ok:
        shutil.rmtree(backup_dir, ignore_errors=True)
        raise HTTPException(400, f"Invalid database: {msg}")

    # Get size
    size_mb = round(
        sum(f.stat().st_size for f in backup_dir.rglob("*") if f.is_file()) / 1e6, 2)

    db.log_activity(
        "backup_uploaded", "backup", None,
        f"Uploaded backup ({size_mb} MB): {file.filename}",
        {"filename": file.filename, "backup_name": backup_name, "size_mb": size_mb},
    )
    return {
        "ok": True,
        "backup_name": backup_name,
        "size_mb": size_mb,
        "verified": ok,
        "message": msg,
        "filename": file.filename,
    }


@router.post("/api/backup/restore")
def restore_backup(payload: dict) -> Any:
    """Restore the database from a backup directory.

    SAFETY: Creates a fresh safety backup of the current DB first,
    then replaces the live DB with the backup's billbook.db.

    Body: {"name": "<backup_dir_name>", "manager_pin": "..."}
    """
    import shutil, sqlite3
    from ..config import DATA
    from .. import shop as shop_mod

    backup_name = payload.get("name", "")
    if not backup_name:
        raise HTTPException(400, "Backup name required")

    # v8.16.6: require manager PIN for restore
    mgr_pin = payload.get("manager_pin", "")
    mgr = shop_mod.verify_manager_pin(mgr_pin)
    if not mgr:
        raise HTTPException(403, "Manager PIN required to restore a backup")

    # Prevent path traversal — only allow alphanumeric + underscore
    if not all(c.isalnum() or c in "_-." for c in backup_name):
        raise HTTPException(400, "Invalid backup name")

    backup_dir = BACKUPS / backup_name
    if not backup_dir.is_dir():
        raise HTTPException(404, f"Backup '{backup_name}' not found")

    backup_db = backup_dir / "billbook.db"
    if not backup_db.exists():
        raise HTTPException(404, "Backup is missing billbook.db")

    # Verify the backup DB is valid before doing anything destructive
    ok, msg = _verify_sqlite_db(str(backup_db))
    if not ok:
        raise HTTPException(400, f"Backup database is corrupt: {msg}")

    # v8.19.1: snapshot THIS machine's license so the restore can't wipe or
    # replace it. The UI's Settings → Backups → Restore flow goes through
    # THIS endpoint (not /api/maintenance/restore), so without this a backup
    # made on a different PC would strip the local license and force the
    # user to re-activate. Same policy as maintenance.restore_backup:
    # the license NEVER travels with a restore.
    from .. import licensing as _licensing
    saved_license = _licensing.local_license_settings()

    # SAFETY: Create a fresh safety backup of the current DB
    safety_dir = BACKUPS / f"pre_restore_safety_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    safety_dir.mkdir(parents=True, exist_ok=True)
    safety_db = safety_dir / "billbook.db"
    try:
        shutil.copy2(db.DB_PATH, safety_db)
        # Also copy uploads + pages if they exist
        for src_dir in (UPLOADS, PAGES):
            if src_dir.exists():
                shutil.copytree(src_dir, safety_dir / src_dir.name, dirs_exist_ok=True)
    except Exception as e:
        shutil.rmtree(safety_dir, ignore_errors=True)
        raise HTTPException(500, f"Could not create safety backup: {e}")

    # Replace the live DB
    # Step 1: close all DB connections (the db module's write_tx uses a connection pool)
    # The db module creates connections per-thread, so we need to invalidate them.
    # Simpler approach: use SQLite's backup API to copy backup_db → live_db in-place.
    try:
        # Close any open connections in the pool
        try:
            db._close_all_connections()  # may not exist; try
        except (AttributeError, Exception):
            pass
        # Use SQLite backup API
        src_conn = sqlite3.connect(str(backup_db))
        dst_conn = sqlite3.connect(str(db.DB_PATH))
        src_conn.backup(dst_conn)
        dst_conn.close()
        src_conn.close()
        # Restore uploads + pages if present in the backup
        for sub_name in ("uploads", "pages"):
            sub = backup_dir / sub_name
            if sub.exists():
                target = UPLOADS if sub_name == "uploads" else PAGES
                if target.exists():
                    shutil.rmtree(target)
                shutil.copytree(sub, target)
    except Exception as e:
        # ROLLBACK: restore from safety backup
        try:
            shutil.copy2(safety_db, db.DB_PATH)
        except Exception:
            pass
        raise HTTPException(500, f"Restore failed (rolled back to safety backup): {e}")

    # Re-apply this machine's own license over whatever the backup carried.
    # Delete-first inside reapply: a legacy backup's foreign license rows are
    # wiped, then the local snapshot (if any) is written back verbatim —
    # licensed machines stay licensed, unlicensed machines stay unlicensed.
    license_reapplied = _licensing.reapply_license_settings(saved_license)

    size_mb = round(
        sum(f.stat().st_size for f in backup_dir.rglob("*") if f.is_file()) / 1e6, 2)
    db.log_activity(
        "backup_restored", "backup", None,
        f"Restored backup '{backup_name}' ({size_mb} MB) by {mgr['name']}",
        {"backup_name": backup_name, "size_mb": size_mb,
         "safety_backup": safety_dir.name,
         "license_settings_reapplied": license_reapplied},
    )
    return {
        "ok": True,
        "restored_from": backup_name,
        "size_mb": size_mb,
        "safety_backup": safety_dir.name,
        "license_preserved": bool(saved_license),
        "license_settings_reapplied": license_reapplied,
        "message": "Restore successful. Please restart the server to apply all changes.",
    }


@router.get("/api/backup/download")
def download_backup(name: str) -> Any:
    """Download a backup as a ZIP file. Used to move backups between machines."""
    import zipfile, io, tempfile
    from fastapi.responses import FileResponse

    if not all(c.isalnum() or c in "_-." for c in name):
        raise HTTPException(400, "Invalid backup name")

    backup_dir = BACKUPS / name
    if not backup_dir.is_dir():
        raise HTTPException(404, f"Backup '{name}' not found")

    # Build a zip in a temp file
    tmp_zip = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
    tmp_zip.close()
    try:
        with zipfile.ZipFile(tmp_zip.name, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in backup_dir.rglob("*"):
                if f.is_file():
                    arcname = f.relative_to(backup_dir.parent)
                    zf.write(f, arcname)
        return FileResponse(
            tmp_zip.name,
            media_type="application/zip",
            filename=f"billbook_backup_{name}.zip",
        )
    except Exception as e:
        os.unlink(tmp_zip.name)
        raise HTTPException(500, f"Could not create zip: {e}")
