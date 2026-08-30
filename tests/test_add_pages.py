"""Tests for the 'Add Images' flow on the bill edit page.

User report (v8.18.5 work):
  "On edit bill when user add image it doesn't add and its items are not
   extracted"

Root causes found (all fixed):
  1. POST /api/bills/{id}/add-pages saved page images but NEVER ran AI
     extraction — no bill_items rows were created for the new pages.
  2. Page numbers collided: every rendered page of file i got
     page_no = existing_max + i + 1 (file index, not a running counter), so a
     multi-page PDF collapsed onto a single page number.
  3. (Frontend) after adding pages the code called navigate() with the SAME
     hash — no hashchange event fired, so the page never re-rendered and the
     new image never appeared. Covered by the job-based response below.

The endpoint is now an async job (like /api/upload-async): it returns
{job_id, bill_id} and streams progress via GET /api/jobs/{job_id}.
"""
import io
import time

from PIL import Image


def _png_bytes(color=(200, 30, 30), size=(80, 120)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def _pdf_bytes(n_pages=3) -> bytes:
    """Create a real multi-page PDF (PyMuPDF)."""
    import fitz
    buf = io.BytesIO()
    doc = fitz.open()
    for i in range(n_pages):
        page = doc.new_page(width=200, height=300)
        page.insert_text((20, 50), f"PDF page {i + 1}")
    doc.save(buf)
    doc.close()
    return buf.getvalue()


def _fake_extraction(pages, on_progress=None):
    """Deterministic fake AI result: one item per page."""
    return (
        {
            "lines": [
                {
                    "raw": f"Item from page {i + 1}",
                    "price": 100.0 * (i + 1),
                    "qty_as_written": 2,
                    "unit": "pcs",
                    "page_no": i + 1,
                    "confidence": 0.95,
                    "sell_price": 250,
                }
                for i in range(len(pages))
            ],
            "written_total": None,
            "phone": None,
            "supplier_guess": None,
            "bill_date": None,
        },
        "fake-provider",
    )


def _create_bill(client, monkeypatch, n_pages=1):
    """Create a bill via /api/upload with mocked extraction."""
    from app import extract as extract_mod
    monkeypatch.setattr(extract_mod, "extract", _fake_extraction)
    files = [("files", (f"p{i}.png", _png_bytes(), "image/png")) for i in range(n_pages)]
    r = client.post("/api/upload", files=files)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _wait_job(client, job_id, timeout=15.0):
    """Poll the job endpoint until done/error (background task runs on the
    TestClient's event loop)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/api/jobs/{job_id}")
        assert r.status_code == 200, r.text
        j = r.json()
        if j["status"] in ("done", "error"):
            return j
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish within {timeout}s")


def _add_pages(client, monkeypatch, bill_id, files):
    """POST add-pages with mocked extraction and wait for the job."""
    from app import extract as extract_mod
    monkeypatch.setattr(extract_mod, "extract", _fake_extraction)
    r = client.post(f"/api/bills/{bill_id}/add-pages", files=files)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "job_id" in body, f"expected async job response, got: {body}"
    job = _wait_job(client, body["job_id"])
    assert job["status"] == "done", f"add-pages job failed: {job.get('error')}"
    return job


def test_add_pages_extracts_items(authed_client, monkeypatch):
    """Adding pages to an existing bill MUST extract items for those pages."""
    bill_id = _create_bill(authed_client, monkeypatch, n_pages=1)

    from app import db
    with db.conn() as c:
        n_items_before = c.execute(
            "SELECT COUNT(*) AS n FROM bill_items WHERE bill_id=?", (bill_id,)
        ).fetchone()["n"]
    assert n_items_before == 1  # initial upload extracted 1 item

    files = [
        ("files", ("extra1.png", _png_bytes((30, 200, 30)), "image/png")),
        ("files", ("extra2.png", _png_bytes((30, 30, 200)), "image/png")),
    ]
    job = _add_pages(authed_client, monkeypatch, bill_id, files)
    assert job["result"]["added_pages"] == 2
    assert job["result"]["items_extracted"] == 2

    with db.conn() as c:
        n_pages_after = c.execute(
            "SELECT COUNT(*) AS n FROM bill_pages WHERE bill_id=?", (bill_id,)
        ).fetchone()["n"]
        n_items_after = c.execute(
            "SELECT COUNT(*) AS n FROM bill_items WHERE bill_id=?", (bill_id,)
        ).fetchone()["n"]
        # New items carry page numbers past the existing pages
        new_items = c.execute(
            "SELECT page_no FROM bill_items WHERE bill_id=? AND page_no IS NOT NULL "
            "ORDER BY page_no", (bill_id,),
        ).fetchall()

    assert n_pages_after == 3, "pages should be added"
    assert n_items_after == 3, f"expected 3 items (1 old + 2 new), got {n_items_after}"
    page_nos = [r["page_no"] for r in new_items]
    assert 2 in page_nos and 3 in page_nos, (
        f"new items must reference pages 2 and 3 — got {page_nos}"
    )


def test_add_pages_page_numbers_sequential(authed_client, monkeypatch):
    """Page numbers must be sequential with no collisions (multi-file)."""
    bill_id = _create_bill(authed_client, monkeypatch, n_pages=1)

    files = [
        ("files", ("extra1.png", _png_bytes((30, 200, 30)), "image/png")),
        ("files", ("extra2.png", _png_bytes((30, 30, 200)), "image/png")),
    ]
    _add_pages(authed_client, monkeypatch, bill_id, files)

    from app import db
    with db.conn() as c:
        rows = c.execute(
            "SELECT page_no, COUNT(*) AS n FROM bill_pages WHERE bill_id=? GROUP BY page_no",
            (bill_id,),
        ).fetchall()
        nos = sorted(r["page_no"] for r in rows)
        dupes = [r["page_no"] for r in rows if r["n"] > 1]
    assert not dupes, f"duplicate page_no values: {dupes}"
    assert nos == [1, 2, 3], f"expected page numbers 1,2,3 — got {nos}"


def test_add_pages_pdf_page_numbers_sequential(authed_client, monkeypatch):
    """A multi-page PDF added to an existing bill must get DISTINCT page
    numbers (old code gave all its pages the same page_no)."""
    bill_id = _create_bill(authed_client, monkeypatch, n_pages=1)

    files = [("files", ("extra.pdf", _pdf_bytes(3), "application/pdf"))]
    job = _add_pages(authed_client, monkeypatch, bill_id, files)
    assert job["result"]["added_pages"] == 3

    from app import db
    with db.conn() as c:
        rows = c.execute(
            "SELECT page_no, COUNT(*) AS n FROM bill_pages WHERE bill_id=? GROUP BY page_no",
            (bill_id,),
        ).fetchall()
        nos = sorted(r["page_no"] for r in rows)
        dupes = [r["page_no"] for r in rows if r["n"] > 1]
    assert not dupes, f"BUG: PDF pages share page_no {dupes}"
    assert nos == [1, 2, 3, 4], f"expected page numbers 1..4 — got {nos}"


def test_add_pages_preserves_user_edits(authed_client, monkeypatch):
    """Bill-level fields the user already filled must NOT be overwritten by
    the new extraction (fill-if-empty only)."""
    bill_id = _create_bill(authed_client, monkeypatch, n_pages=1)

    from app import db
    with db.conn() as c:
        c.execute(
            "UPDATE bills SET supplier_name='My Manual Supplier', bill_date='2026-01-05' "
            "WHERE id=?", (bill_id,),
        )

    # Extraction "finds" a supplier — it must not clobber the manual value
    def _ex_with_supplier(pages, on_progress=None):
        data, provider = _fake_extraction(pages)
        data["supplier_guess"] = "AI Guessed Supplies"
        data["bill_date"] = "2020-06-06"
        return data, provider

    from app import extract as extract_mod
    monkeypatch.setattr(extract_mod, "extract", _ex_with_supplier)
    files = [("files", ("extra.png", _png_bytes((9, 90, 9)), "image/png"))]
    r = authed_client.post(f"/api/bills/{bill_id}/add-pages", files=files)
    assert r.status_code == 200, r.text
    job = _wait_job(authed_client, r.json()["job_id"])
    assert job["status"] == "done"

    with db.conn() as c:
        bill = c.execute(
            "SELECT supplier_name, bill_date, status FROM bills WHERE id=?", (bill_id,)
        ).fetchone()
    assert bill["supplier_name"] == "My Manual Supplier"
    assert bill["bill_date"] == "2026-01-05"
    assert bill["status"] == "review", "bill must go back to review after new pages"


def test_add_pages_extraction_failure_still_adds_pages(authed_client, monkeypatch):
    """If AI extraction fails, pages are still added and the job succeeds
    with a warning — the user can enter items manually."""
    bill_id = _create_bill(authed_client, monkeypatch, n_pages=1)

    def _boom(pages):
        raise RuntimeError("no AI providers configured")

    from app import extract as extract_mod
    monkeypatch.setattr(extract_mod, "extract", _boom)
    files = [("files", ("extra.png", _png_bytes((9, 90, 9)), "image/png"))]
    r = authed_client.post(f"/api/bills/{bill_id}/add-pages", files=files)
    assert r.status_code == 200, r.text
    job = _wait_job(authed_client, r.json()["job_id"])
    assert job["status"] == "done", "extraction failure must not fail the job"
    assert job["result"]["added_pages"] == 1
    assert job["result"]["items_extracted"] == 0

    from app import db
    with db.conn() as c:
        n_pages = c.execute(
            "SELECT COUNT(*) AS n FROM bill_pages WHERE bill_id=?", (bill_id,)
        ).fetchone()["n"]
    assert n_pages == 2


def test_add_pages_to_missing_bill_404(authed_client):
    files = [("files", ("x.png", _png_bytes(), "image/png"))]
    r = authed_client.post("/api/bills/999999/add-pages", files=files)
    assert r.status_code == 404


def test_add_pages_rejects_bad_extension(authed_client, monkeypatch):
    bill_id = _create_bill(authed_client, monkeypatch, n_pages=1)
    files = [("files", ("evil.exe", b"MZ9999999", "application/octet-stream"))]
    r = authed_client.post(f"/api/bills/{bill_id}/add-pages", files=files)
    assert r.status_code == 400
