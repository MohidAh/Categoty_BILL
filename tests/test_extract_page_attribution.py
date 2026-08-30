"""v8.18.5 regression: page attribution when adding images to a bill.

User report: "when on edit we add image, all items show under that image
instead of proper image-wise."

Root cause: providers (call_gemini / call_openai_style) send every page in
ONE request and return lines with NO page_no. Batches of <= 2 pages skipped
_extract_chunked (the page-by-page path that sets page_no per chunk), so a
2-page batch came back as one unattributed blob — add-pages then dumped
every item onto the first new page (and the original upload flow stored
NULL page_no for every item).

These tests stub the PROVIDER level (not extract.extract) so the real
routing/chunking code runs — exactly what the earlier add-pages tests
missed by stubbing extract() with items that already had page_no set.
"""
from pathlib import Path

import pytest

from app import extract as extract_mod


def _fake_provider(pages):
    """Mimic a real provider: one line per page, NO page_no in the output
    (the AI is never asked for page attribution)."""
    return {
        "phone": None,
        "supplier_guess": None,
        "bill_date": None,
        "written_total": None,
        "lines": [
            {
                "raw": f"item of {pages[0].name}",
                "price": 100.0,
                "qty_as_written": 1,
                "unit": "pcs",
                "confidence": 0.9,
                "sell_price": None,
            }
        ],
        "line_confidence": [0.9],
    }


def _pages(tmp_path, n):
    return [tmp_path / f"page{i}.png" for i in range(1, n + 1)]


@pytest.fixture(autouse=True)
def _no_chunk_pause(monkeypatch):
    """_extract_chunked sleeps 3s between chunks — skip it in tests."""
    monkeypatch.setattr("time.sleep", lambda s: None)


def _stub_providers(monkeypatch, n_providers=1):
    """Patch _providers() to return fake providers (same priority)."""
    monkeypatch.setattr(
        extract_mod, "_providers",
        lambda: [(0, f"fake{i}", _fake_provider) for i in range(n_providers)],
    )


def test_two_pages_get_distinct_page_nos(tmp_path, monkeypatch):
    """THE bug: a 2-page batch must attribute lines to pages 1 and 2,
    not dump everything on one page."""
    _stub_providers(monkeypatch, n_providers=1)
    data, _name = extract_mod.extract(_pages(tmp_path, 2))
    raws = {ln["raw"]: ln.get("page_no") for ln in data["lines"]}
    assert raws == {"item of page1.png": 1, "item of page2.png": 2}


def test_three_pages_regression(tmp_path, monkeypatch):
    """3+ pages were already chunked — must keep working."""
    _stub_providers(monkeypatch, n_providers=1)
    data, _name = extract_mod.extract(_pages(tmp_path, 3))
    raws = {ln["raw"]: ln.get("page_no") for ln in data["lines"]}
    assert raws == {
        "item of page1.png": 1,
        "item of page2.png": 2,
        "item of page3.png": 3,
    }


def test_single_page_gets_page_no_1(tmp_path, monkeypatch):
    """Single-page batches previously stored NULL page_no for every item —
    the edit page then showed them without a Page 1 section header."""
    _stub_providers(monkeypatch, n_providers=1)
    data, _name = extract_mod.extract(_pages(tmp_path, 1))
    assert len(data["lines"]) == 1
    assert data["lines"][0]["page_no"] == 1


def test_multi_provider_two_pages_each(tmp_path, monkeypatch):
    """Round-robin split: 2 providers x 4 pages -> each gets 2 pages.
    Previously a 2-page subset went out as ONE unattributed call and the
    len(page_nos)==1 guard skipped tagging entirely -> NULL page_no."""
    _stub_providers(monkeypatch, n_providers=2)
    data, _name = extract_mod.extract(_pages(tmp_path, 4))
    raws = {ln["raw"]: ln.get("page_no") for ln in data["lines"]}
    # provider A gets pages 1,3; provider B gets pages 2,4 (round-robin)
    assert raws == {
        "item of page1.png": 1,
        "item of page2.png": 2,
        "item of page3.png": 3,
        "item of page4.png": 4,
    }


def test_add_pages_attributes_items_per_image(authed_client, monkeypatch):
    """End-to-end through upload + add-pages jobs with PROVIDER-level stub:
    a 1-page bill + 2 added images must put each image's item under its
    own page (2 and 3), not all under the first added image."""
    from tests.test_add_pages import _png_bytes, _wait_job

    # Provider-level stub ONLY — the real extract() routing must run.
    _stub_providers(monkeypatch, n_providers=1)

    # 1. Create the bill with a single image (item lands on page 1)
    files = [("files", ("p0.png", _png_bytes(), "image/png"))]
    r = authed_client.post("/api/upload", files=files)
    assert r.status_code == 200, r.text
    bill_id = r.json()["id"]

    # 2. Add two more images in one call
    files = [
        ("files", ("a.png", _png_bytes(), "image/png")),
        ("files", ("b.png", _png_bytes(), "image/png")),
    ]
    r = authed_client.post(f"/api/bills/{bill_id}/add-pages", files=files)
    assert r.status_code == 200, r.text
    job = _wait_job(authed_client, r.json()["job_id"])
    assert job["status"] == "done", f"add-pages job failed: {job.get('error')}"

    # 3. Each added image's item must sit on ITS OWN page number
    from app import db
    with db.conn() as c:
        n_pages = c.execute(
            "SELECT COUNT(*) AS n FROM bill_pages WHERE bill_id=?", (bill_id,)
        ).fetchone()["n"]
        rows = c.execute(
            "SELECT raw, page_no FROM bill_items WHERE bill_id=? ORDER BY page_no",
            (bill_id,),
        ).fetchall()
    assert n_pages == 3, f"expected 3 pages, got {n_pages}"
    assert len(rows) == 3, f"expected 3 items (one per page), got {len(rows)}"
    pages_seen = [r["page_no"] for r in rows]
    assert all(p is not None for p in pages_seen), f"NULL page_no in: {pages_seen}"
    assert pages_seen == [1, 2, 3], f"items not attributed image-wise: {pages_seen}"
    by_page = {r["page_no"]: r["raw"] for r in rows}
    assert by_page[2] != by_page[3], (
        f"pages 2 and 3 hold the same item ({by_page}) — "
        "all items collapsed onto one image"
    )
