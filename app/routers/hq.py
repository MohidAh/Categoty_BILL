"""v8.0 Phase 2-3, 6 — HQ Branch Registry + Consolidated Visibility + Price Push router.

Phase 2:
- POST /api/hq/branches/code  — generate a 6-digit registration code (5-min expiry)
- POST /api/hq/branches/register  — branch presents code + its name/region → HQ issues auth token
- GET  /api/hq/branches  — list all registered branches
- DELETE /api/hq/branches/{id}  — revoke a branch (sets active=0)

Phase 3:
- POST /api/sync/branch-summary  — branch pushes daily summary (Bearer token auth,
  idempotent by branch_id+summary_date)
- GET  /api/hq/owner-hub  — consolidated dashboard data (all branches summed)

Phase 6:
- POST /api/hq/price-push  — HQ creates a price push (returns price_push_id + list of branch tunnel URLs to deliver to)
- POST /api/sync/price-push  — branch receives a price push (Bearer auth, idempotent by price_push_id)
- GET  /api/hq/price-pushes  — list price push history
"""
import secrets, hashlib, json
from datetime import datetime, timedelta
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Any, Optional
from .. import db

router = APIRouter()


# ─── Pairing code generation (HQ side) ──────────────────────────────────────

@router.post("/api/hq/branches/code")
def generate_branch_pairing_code(payload: dict = None) -> Any:
    """HQ generates an 8-digit registration code valid for 2 minutes.

    SECURITY (v8.13.2): Increased from 6-digit/5-min → 8-digit/2-min.
    The /api/hq/branches/register endpoint is also rate-limited + per-code
    lockout after 3 failures.

    Optional body: { proposed_name, proposed_region } — pre-fills the branch's
    registration form when the code is consumed.
    """
    body = payload or {}
    # v8.13.2: 8-digit code (was 6-digit). 100M combinations vs 1M.
    code = str(secrets.randbelow(90000000) + 10000000)  # 8-digit code
    # v8.13.2: 2-minute expiry (was 5 minutes)
    expires = (datetime.now() + timedelta(minutes=2)).strftime("%Y-%m-%d %H:%M:%S")
    with db.conn() as c:
        c.execute(
            "INSERT INTO branch_pairing_codes(code, role, proposed_name, proposed_region, expires_at) "
            "VALUES(?,?,?,?,?)",
            (code, "branch", body.get("proposed_name", ""), body.get("proposed_region", ""), expires),
        )
    db.log_activity("branch_pairing_code_generated", "branch_pairing", None,
                    f"Branch pairing code generated", {})
    return {"code": code, "expires_in": 120}  # 2 minutes


@router.get("/api/hq/branches/qr")
def generate_branch_qr(request: Request) -> Any:
    """v8.1 Phase 3: Generate a QR code for branch registration.

    Returns a PNG image. The QR encodes JSON: {type, hq_url, registration_code}.
    The branch scans this → auto-submits registration → HQ approves.
    """
    import qrcode, io
    from fastapi.responses import StreamingResponse
    code_r = generate_branch_pairing_code({})
    hq_url = str(request.base_url).rstrip("/")
    qr_payload = json.dumps({
        "type": "billbook_branch_registration",
        "hq_url": hq_url,
        "registration_code": code_r["code"],
    })
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(qr_payload)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/png",
                             headers={"X-Registration-Code": code_r["code"],
                                      "X-HQ-Url": hq_url})


# ─── Branch registration (branch → HQ) ──────────────────────────────────────

class BranchRegisterIn(BaseModel):
    code: str
    branch_name: str
    region: str = ""
    branch_id: str  # the branch's local branch_id from branch_config
    tunnel_url: str = ""  # branch's Cloudflare Tunnel URL (so HQ can push to it)


@router.post("/api/hq/branches/register")
def register_branch(payload: BranchRegisterIn, request: Request) -> Any:
    """Branch presents its 8-digit code + identity → HQ issues a per-branch auth token.

    The branch stores this token locally (in branch_config.sync_token_hash) and
    includes it as a Bearer token on all sync calls to HQ.

    SECURITY (v8.13.2): Added per-IP rate limiting + per-code failure lockout
    (after 3 failures, the code is invalidated to force regeneration).
    """
    # v8.13.2: Rate limit per IP (this endpoint is public — no session required)
    from ..security import check_login_throttle, record_failed_login
    client_ip = request.client.host if request.client else "unknown"
    if not check_login_throttle(client_ip):
        raise HTTPException(429, "Too many registration attempts. Wait 60 seconds.")
    code = (payload.code or "").strip()
    # v8.13.2: 8-digit code (was 6-digit)
    if not code or len(code) != 8 or not code.isdigit():
        raise HTTPException(400, "Invalid code format (must be 8 digits)")
    with db.conn() as c:
        row = c.execute(
            "SELECT * FROM branch_pairing_codes WHERE code=? AND used=0 "
            "AND expires_at > datetime('now','localtime') ORDER BY id DESC LIMIT 1",
            (code,),
        ).fetchone()
        if not row:
            record_failed_login(client_ip)
            # v8.13.2: per-code lockout — 3 failures invalidates the code
            existing = c.execute(
                "SELECT id, failure_count FROM branch_pairing_codes WHERE code=? ORDER BY id DESC LIMIT 1",
                (code,),
            ).fetchone()
            if existing:
                new_count = (existing["failure_count"] or 0) + 1
                if new_count >= 3:
                    c.execute(
                        "UPDATE branch_pairing_codes SET used=1, failure_count=? WHERE id=?",
                        (new_count, existing["id"]),
                    )
                else:
                    c.execute(
                        "UPDATE branch_pairing_codes SET failure_count=? WHERE id=?",
                        (new_count, existing["id"]),
                    )
            raise HTTPException(403, "Invalid or expired pairing code")
        # Mark code as used (single-use)
        c.execute("UPDATE branch_pairing_codes SET used=1 WHERE id=?", (row["id"],))
        # Generate a per-branch auth token
        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        # Check if branch_id already registered (re-registration replaces)
        existing = c.execute(
            "SELECT id FROM branches WHERE branch_id=?", (payload.branch_id,)
        ).fetchone()
        if existing:
            c.execute(
                "UPDATE branches SET name=?, region=?, tunnel_url=?, auth_token_hash=?, active=1 "
                "WHERE branch_id=?",
                (payload.branch_name, payload.region, payload.tunnel_url, token_hash, payload.branch_id),
            )
            branch_db_id = existing["id"]
        else:
            cur = c.execute(
                "INSERT INTO branches(branch_id, name, region, tunnel_url, auth_token_hash) "
                "VALUES(?,?,?,?,?)",
                (payload.branch_id, payload.branch_name, payload.region, payload.tunnel_url, token_hash),
            )
            branch_db_id = cur.lastrowid
    db.log_activity("branch_registered", "branch", branch_db_id,
                    f"Branch '{payload.branch_name}' registered (branch_id={payload.branch_id})",
                    {"branch_id": payload.branch_id, "name": payload.branch_name})
    return {"token": raw_token, "branch_id": payload.branch_id, "name": payload.branch_name}


# ─── List + revoke ──────────────────────────────────────────────────────────

@router.get("/api/hq/branches")
def list_branches(active_only: bool = False) -> Any:
    """List all registered branches. Never returns auth_token_hash."""
    with db.conn() as c:
        if active_only:
            rows = c.execute(
                "SELECT id, branch_id, name, region, tunnel_url, last_seen, active, created_at "
                "FROM branches WHERE active=1 ORDER BY name"
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT id, branch_id, name, region, tunnel_url, last_seen, active, created_at "
                "FROM branches ORDER BY active DESC, name"
            ).fetchall()
    return {"branches": [dict(r) for r in rows], "count": len(rows)}


@router.delete("/api/hq/branches/{branch_db_id}")
def revoke_branch(branch_db_id: int) -> Any:
    """Revoke a branch (set active=0). The branch's token is no longer valid for sync."""
    with db.conn() as c:
        cur = c.execute(
            "UPDATE branches SET active=0 WHERE id=? AND active=1",
            (branch_db_id,)
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "Branch not found or already revoked")
        row = c.execute("SELECT branch_id, name FROM branches WHERE id=?", (branch_db_id,)).fetchone()
    db.log_activity("branch_revoked", "branch", branch_db_id,
                    f"Branch '{row['name']}' (branch_id={row['branch_id']}) revoked",
                    {"branch_id": row["branch_id"]})
    return {"ok": True}


# ─── Token verification helper (used by sync endpoints in Phase 3+) ─────────

def verify_branch_token(request: Request) -> Any:
    """Verify the Bearer token on sync endpoints. Returns the branch row.

    On HQ: looks up the token in the `branches` table (HQ-side registry).
    On a branch: looks up the token in `branch_config.sync_token_hash` (the
    branch's own stored token, issued by HQ during registration).

    Raises HTTPException(401) if the token is missing/invalid.
    """
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401, "Missing Bearer token")
    raw_token = auth[7:].strip()
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    with db.conn() as c:
        # First try HQ-side: look up in branches table
        row = c.execute(
            "SELECT * FROM branches WHERE auth_token_hash=? AND active=1",
            (token_hash,)
        ).fetchone()
        if row:
            # Update last_seen + re-fetch
            c.execute(
                "UPDATE branches SET last_seen=datetime('now','localtime') WHERE id=?",
                (row["id"],)
            )
            row = c.execute(
                "SELECT * FROM branches WHERE id=?", (row["id"],)
            ).fetchone()
            return dict(row)
        # Branch-side: look up in branch_config (the token HQ issued to us)
        cfg = c.execute(
            "SELECT branch_id, branch_name, sync_token_hash FROM branch_config WHERE id=1"
        ).fetchone()
        if cfg and cfg["sync_token_hash"] == token_hash:
            return {
                "branch_id": cfg["branch_id"],
                "name": cfg["branch_name"],
                "auth_token_hash": cfg["sync_token_hash"],
            }
    raise HTTPException(401, "Invalid or revoked branch token")


# ─── Phase 3: Branch Summary Sync ───────────────────────────────────────────

class BranchSummaryIn(BaseModel):
    summary_date: str  # YYYY-MM-DD
    sales: float = 0
    cogs: float = 0
    gross_profit: float = 0
    expenses: float = 0
    cash_in_drawer: float = 0
    stock_snapshot: dict = {}  # {category_id: {qty, value, avg_cost}}


@router.post("/api/sync/branch-summary")
def receive_branch_summary(payload: BranchSummaryIn, request: Request) -> Any:
    """Branch pushes a daily summary to HQ. Bearer-token authenticated.

    Idempotent by (branch_id, summary_date) — re-delivery updates the same row.
    """
    branch = verify_branch_token(request)
    with db.conn() as c:
        # UPSERT on (branch_id, summary_date)
        existing = c.execute(
            "SELECT id FROM branch_summaries WHERE branch_id=? AND summary_date=?",
            (branch["branch_id"], payload.summary_date),
        ).fetchone()
        if existing:
            c.execute(
                "UPDATE branch_summaries SET sales=?, cogs=?, gross_profit=?, expenses=?, "
                "cash_in_drawer=?, stock_snapshot_json=?, synced_at=datetime('now','localtime') "
                "WHERE id=?",
                (payload.sales, payload.cogs, payload.gross_profit, payload.expenses,
                 payload.cash_in_drawer, json.dumps(payload.stock_snapshot), existing["id"]),
            )
        else:
            c.execute(
                "INSERT INTO branch_summaries(branch_id, summary_date, sales, cogs, "
                "gross_profit, expenses, cash_in_drawer, stock_snapshot_json) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (branch["branch_id"], payload.summary_date, payload.sales, payload.cogs,
                 payload.gross_profit, payload.expenses, payload.cash_in_drawer,
                 json.dumps(payload.stock_snapshot)),
            )
    return {"ok": True, "branch_id": branch["branch_id"], "summary_date": payload.summary_date}


@router.get("/api/hq/owner-hub")
def owner_hub_dashboard(date: str = "") -> Any:
    """Consolidated dashboard: all branches summed for the given date (default: today).

    Returns:
    - consolidated: {sales, cogs, gross_profit, expenses, cash_in_drawer} summed across branches
    - leaderboard: [{branch_id, name, region, sales, gross_profit, last_seen, stale}] sorted by sales desc
    - branches: per-branch breakdown with stock_snapshot + stale flag (no sync in 24h)
    - date: the date queried
    """
    if not date:
        date = datetime.now().strftime("%Y-%m-%d")
    with db.conn() as c:
        # Get all registered branches
        branch_rows = c.execute(
            "SELECT id, branch_id, name, region, tunnel_url, last_seen, active "
            "FROM branches ORDER BY name"
        ).fetchall()
        # Get summaries for the date
        summary_rows = c.execute(
            "SELECT * FROM branch_summaries WHERE summary_date=?", (date,)
        ).fetchall()
        summary_by_branch = {s["branch_id"]: dict(s) for s in summary_rows}
    now = datetime.now()
    consolidated = {"sales": 0, "cogs": 0, "gross_profit": 0, "expenses": 0, "cash_in_drawer": 0}
    leaderboard = []
    branches_out = []
    for b in branch_rows:
        if not b["active"]:
            continue
        s = summary_by_branch.get(b["branch_id"])
        sales = s["sales"] if s else 0
        gp = s["gross_profit"] if s else 0
        cogs = s["cogs"] if s else 0
        expenses = s["expenses"] if s else 0
        cash = s["cash_in_drawer"] if s else 0
        stock = {}
        if s and s["stock_snapshot_json"]:
            try:
                stock = json.loads(s["stock_snapshot_json"])
            except Exception:
                stock = {}
        # Stale = no sync in 24h (or never synced)
        stale = True
        if b["last_seen"]:
            try:
                last = datetime.strptime(b["last_seen"], "%Y-%m-%d %H:%M:%S")
                stale = (now - last).total_seconds() > 86400
            except Exception:
                stale = True
        consolidated["sales"] += sales
        consolidated["cogs"] += cogs
        consolidated["gross_profit"] += gp
        consolidated["expenses"] += expenses
        consolidated["cash_in_drawer"] += cash
        leaderboard.append({
            "branch_id": b["branch_id"], "name": b["name"], "region": b["region"],
            "sales": sales, "gross_profit": gp, "last_seen": b["last_seen"], "stale": stale,
        })
        branches_out.append({
            "branch_id": b["branch_id"], "name": b["name"], "region": b["region"],
            "sales": sales, "cogs": cogs, "gross_profit": gp, "expenses": expenses,
            "cash_in_drawer": cash, "stock_snapshot": stock,
            "last_seen": b["last_seen"], "stale": stale,
            "synced_today": s is not None,
        })
    # Sort leaderboard by sales desc
    leaderboard.sort(key=lambda x: x["sales"], reverse=True)
    return {
        "date": date,
        "consolidated": consolidated,
        "leaderboard": leaderboard,
        "branches": branches_out,
        "branch_count": len(branches_out),
        "active_branches_synced_today": sum(1 for b in branches_out if b["synced_today"]),
    }


# ─── Phase 6: Global Price Push ─────────────────────────────────────────────

class PricePushIn(BaseModel):
    category_id: int
    new_sell_price: float
    notes: str = ""


@router.post("/api/hq/price-push")
def create_price_push(payload: PricePushIn) -> Any:
    """HQ creates a price push. Returns the price_push_id + the list of branch
    tunnel_urls that HQ should deliver to.

    HQ then calls each branch's /api/sync/price-push endpoint with this price_push_id.
    The branch applies it idempotently (re-delivery never double-applies).
    """
    if payload.new_sell_price <= 0:
        raise HTTPException(400, "Price must be positive")
    price_push_id = "PP-" + datetime.now().strftime("%Y%m%d") + "-" + secrets.token_hex(3).upper()
    # Look up the category code
    with db.conn() as c:
        cat_row = c.execute("SELECT code FROM price_categories WHERE id=?", (payload.category_id,)).fetchone()
        if not cat_row:
            raise HTTPException(404, f"Category {payload.category_id} not found")
        category_code = cat_row["code"]
        # Record the push on HQ
        c.execute(
            "INSERT INTO price_pushes(price_push_id, category_id, category_code, "
            "new_sell_price, notes) VALUES(?,?,?,?,?)",
            (price_push_id, payload.category_id, category_code,
             payload.new_sell_price, payload.notes),
        )
        # Get the list of active branch tunnel_urls to deliver to
        branch_rows = c.execute(
            "SELECT branch_id, name, tunnel_url FROM branches WHERE active=1 AND tunnel_url != ''"
        ).fetchall()
    db.log_activity(
        "price_push_created", "price_push", None,
        f"Price push {price_push_id}: Cat {category_code} → Rs {payload.new_sell_price}",
        {"price_push_id": price_push_id, "category_id": payload.category_id,
         "new_sell_price": payload.new_sell_price},
    )
    return {
        "price_push_id": price_push_id,
        "category_id": payload.category_id,
        "category_code": category_code,
        "new_sell_price": payload.new_sell_price,
        "notes": payload.notes,
        "delivery_targets": [
            {"branch_id": b["branch_id"], "name": b["name"], "tunnel_url": b["tunnel_url"]}
            for b in branch_rows
        ],
    }


class PricePushApplyIn(BaseModel):
    price_push_id: str
    category_id: int
    category_code: str = ""
    new_sell_price: float
    notes: str = ""


@router.post("/api/sync/price-push")
def apply_price_push(payload: PricePushApplyIn, request: Request) -> Any:
    """Branch receives a price push from HQ. Bearer-token authenticated.

    Idempotent by price_push_id — re-delivery never double-applies. The first
    delivery updates the local price_categories.sell_price + logs to activity_log
    with source='hq'. Subsequent deliveries with the same price_push_id return
    success without re-applying.
    """
    branch = verify_branch_token(request)  # also updates last_seen
    with db.conn() as c:
        # Check if this price_push_id was already applied (idempotent)
        existing = c.execute(
            "SELECT id FROM price_pushes WHERE price_push_id=?", (payload.price_push_id,)
        ).fetchone()
        if existing:
            return {"ok": True, "price_push_id": payload.price_push_id,
                    "status": "already_applied", "note": "Idempotent — already applied"}
        # Record the push (with applied_at set)
        c.execute(
            "INSERT INTO price_pushes(price_push_id, category_id, category_code, "
            "new_sell_price, notes, applied_at) VALUES(?,?,?,?,?, datetime('now','localtime'))",
            (payload.price_push_id, payload.category_id, payload.category_code,
             payload.new_sell_price, payload.notes),
        )
        # Apply the price update to local price_categories
        cur = c.execute(
            "UPDATE price_categories SET sell_price=? WHERE id=?",
            (payload.new_sell_price, payload.category_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(404, f"Category {payload.category_id} not found locally")
    db.log_activity(
        "price_push_applied", "price_push", None,
        f"Price push {payload.price_push_id} applied: Cat {payload.category_code} → Rs {payload.new_sell_price}",
        {"price_push_id": payload.price_push_id, "category_id": payload.category_id,
         "new_sell_price": payload.new_sell_price, "source": "hq"},
    )
    return {"ok": True, "price_push_id": payload.price_push_id, "status": "applied"}


@router.get("/api/hq/price-pushes")
def list_price_pushes(limit: int = 50) -> Any:
    """List price push history (HQ-side view of pushes created + branch-side applied)."""
    with db.conn() as c:
        rows = c.execute(
            "SELECT * FROM price_pushes ORDER BY pushed_at DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return {"pushes": [dict(r) for r in rows], "count": len(rows)}


# ─── v8.0.1: Sync outbox status + manual flush ──────────────────────────────

@router.get("/api/sync/outbox")
def sync_outbox_status() -> Any:
    """Return the local sync_outbox status (pending/sent/failed counts + recent entries)."""
    from ..sync import get_outbox_status
    return get_outbox_status()


@router.post("/api/sync/outbox/flush")
def sync_outbox_flush(payload: dict) -> Any:
    """Manually trigger a flush of pending outbox entries.

    Body: { dest_url, bearer_token }
    Returns: { sent, failed, remaining }
    """
    from ..sync import flush_sync_outbox
    dest_url = payload.get("dest_url", "")
    bearer_token = payload.get("bearer_token", "")
    if not dest_url or not bearer_token:
        raise HTTPException(400, "dest_url and bearer_token are required")
    return flush_sync_outbox(dest_url, bearer_token)
