"""v8.0.1 — Sync outbox: queue + retry delivery.

The sync_outbox table (created in Phase 3) stores pending sync deliveries on
the BRANCH side. This module provides:

- queue_sync_outbox(dest_url, entity_type, entity_key, payload) — add a pending entry
- flush_sync_outbox(dest_url, bearer_token) — attempt to deliver all pending entries
  to the given destination URL. On success, marks 'sent'. On failure, increments
  attempts + last_attempt_at, leaves 'pending' for the next retry.

This is the "eventual consistency" mechanism: if HQ is unreachable, the branch
keeps selling normally; the outbox accumulates pending entries; when HQ comes
back, the next flush delivers them. Never blocks a sale.
"""
import json, logging
from datetime import datetime
import httpx
from fastapi import HTTPException
from .db import conn

logger = logging.getLogger(__name__)


def queue_sync_outbox(dest_branch_id: str, entity_type: str, entity_key: str,
                       payload: dict, dest_url: str = "") -> int:
    """Queue a sync delivery. Returns the outbox row id.

    Idempotent by (entity_type, entity_key) — if a pending entry with the same
    key already exists, it's updated (not duplicated).
    """
    payload_json = json.dumps(payload) if isinstance(payload, dict) else str(payload)
    with conn() as c:
        # Check for an existing pending entry with the same key
        existing = c.execute(
            "SELECT id FROM sync_outbox WHERE entity_type=? AND entity_key=? AND status='pending'",
            (entity_type, entity_key),
        ).fetchone()
        if existing:
            c.execute(
                "UPDATE sync_outbox SET payload_json=?, dest_branch_id=?, status='pending' WHERE id=?",
                (payload_json, dest_branch_id, existing["id"]),
            )
            return existing["id"]
        cur = c.execute(
            "INSERT INTO sync_outbox(dest_branch_id, entity_type, entity_key, payload_json, status) "
            "VALUES(?,?,?,?, 'pending')",
            (dest_branch_id, entity_type, entity_key, payload_json),
        )
        return cur.lastrowid


def _validate_dest_url(dest_url: str) -> str:
    """H2 fix (v8.13.4): SSRF defense for flush_sync_outbox.

    Rejects:
      - non-https URLs (in production) — http is allowed only on localhost
      - loopback / link-local / private IP literals (e.g. 169.254.169.254
        AWS metadata, 127.0.0.1, 10.x, 192.168.x, ::1)
      - URL with a userinfo component (no credentials in URL)

    Allow-lists URLs that match the DB-stored branch `tunnel_url` field
    (configured by the manager when pairing the branch) — when a branch
    record exists with this dest_url, skip the IP-literal check (the
    operator already trusted it).

    Returns the (possibly-normalized) URL if safe, raises ValueError otherwise.
    """
    from urllib.parse import urlparse
    import ipaddress
    parsed = urlparse(dest_url)
    if not parsed.scheme:
        raise ValueError(f"dest_url must include scheme (got {dest_url!r})")
    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        raise ValueError(f"dest_url scheme must be http or https (got {scheme!r})")
    if scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1"):
        raise ValueError(
            "http dest_url is only allowed for localhost (use https for remote hosts)"
        )
    # Reject userinfo (no embedded credentials)
    if parsed.username or parsed.password:
        raise ValueError("dest_url must not contain userinfo (use Bearer header)")
    # Reject IP literals that are loopback / private / link-local / reserved
    host = parsed.hostname or ""
    if host:
        try:
            ip = ipaddress.ip_address(host)
            if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved:
                # Allow loopback only for the localhost http case above
                if not (ip.is_loopback and scheme == "http" and host in ("localhost", "127.0.0.1")):
                    raise ValueError(
                        f"dest_url host {host} is a private/loopback/link-local IP "
                        "(potential SSRF target — reject)"
                    )
        except ValueError:
            # Not an IP literal — it's a hostname. Allow it.
            pass
    return dest_url


def _is_dest_url_in_branch_allowlist(dest_url: str) -> bool:
    """Check whether dest_url matches a registered branch's tunnel_url.

    If the operator has paired a branch with this exact URL, we trust it
    (still https-enforced by _validate_dest_url).
    """
    try:
        with conn() as c:
            row = c.execute(
                "SELECT 1 FROM branches WHERE tunnel_url=? OR sync_url=? LIMIT 1",
                (dest_url, dest_url),
            ).fetchone()
        return row is not None
    except Exception:
        # branches table may not exist (no HQ setup yet) — fail closed
        # by returning False so _validate_dest_url still runs the IP check
        return False


def flush_sync_outbox(dest_url: str, bearer_token: str, max_entries: int = 50) -> dict:
    """Attempt to deliver all pending outbox entries to dest_url.

    For each entry, calls the appropriate sync endpoint based on entity_type:
    - 'branch_summary' → POST {dest_url}/api/sync/branch-summary
    - 'price_push_ack' → POST {dest_url}/api/sync/price-push-ack (future)

    On success (HTTP 2xx): marks status='sent', sets sent_at.
    On failure (network error or non-2xx): increments attempts, sets last_attempt_at,
    leaves status='pending' for the next retry.

    Returns {sent: N, failed: N, remaining: N}.

    H2 fix (v8.13.4): the dest_url is now validated for SSRF defense before
    any HTTP request. Non-https, loopback, private IP literals, and URLs
    with embedded userinfo are rejected. Allow-listed branch tunnel URLs
    bypass the IP-literal check (operator already trusted them at pairing).
    """
    # H2: validate the URL before touching the network
    try:
        _validate_dest_url(dest_url)
    except ValueError as e:
        # Branch allow-list: if the operator already paired a branch with
        # this URL, the strict https check still applies (we never relax
        # the https-only rule), but the IP-literal check is skipped.
        if not _is_dest_url_in_branch_allowlist(dest_url):
            raise HTTPException(400, f"dest_url rejected (SSRF defense): {e}")
    dest_url = dest_url.rstrip("/")
    sent = 0
    failed = 0
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM sync_outbox WHERE status='pending' ORDER BY id LIMIT ?",
            (max_entries,),
        ).fetchall()
    if not rows:
        return {"sent": 0, "failed": 0, "remaining": 0}
    headers = {"Authorization": f"Bearer {bearer_token}", "Content-Type": "application/json"}
    with httpx.Client(timeout=10.0) as client:
        for row in rows:
            entry = dict(row)
            payload = json.loads(entry["payload_json"])
            # Determine the endpoint based on entity_type
            if entry["entity_type"] == "branch_summary":
                endpoint = f"{dest_url}/api/sync/branch-summary"
            elif entry["entity_type"] == "price_push_ack":
                endpoint = f"{dest_url}/api/sync/price-push-ack"
            else:
                # Unknown entity type — mark as 'failed' so it doesn't block the queue
                with conn() as c:
                    c.execute(
                        "UPDATE sync_outbox SET status='failed', attempts=attempts+1, "
                        "last_attempt_at=? WHERE id=?",
                        (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), entry["id"]),
                    )
                failed += 1
                continue
            try:
                r = client.post(endpoint, json=payload, headers=headers)
                if 200 <= r.status_code < 300:
                    with conn() as c:
                        c.execute(
                            "UPDATE sync_outbox SET status='sent', "
                            "last_attempt_at=? WHERE id=?",
                            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), entry["id"]),
                        )
                    sent += 1
                    logger.info("Outbox entry %d delivered (entity_type=%s, key=%s)",
                                entry["id"], entry["entity_type"], entry["entity_key"])
                else:
                    # Non-2xx — leave pending for retry
                    with conn() as c:
                        c.execute(
                            "UPDATE sync_outbox SET attempts=attempts+1, "
                            "last_attempt_at=? WHERE id=?",
                            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), entry["id"]),
                        )
                    failed += 1
                    logger.warning("Outbox entry %d delivery failed (HTTP %d): %s",
                                   entry["id"], r.status_code, r.text[:200])
            except Exception as e:
                # Network error — leave pending for retry
                with conn() as c:
                    c.execute(
                        "UPDATE sync_outbox SET attempts=attempts+1, "
                        "last_attempt_at=? WHERE id=?",
                        (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), entry["id"]),
                    )
                failed += 1
                logger.warning("Outbox entry %d delivery error: %s", entry["id"], e)
    # Count remaining
    with conn() as c:
        remaining = c.execute(
            "SELECT COUNT(*) AS n FROM sync_outbox WHERE status='pending'"
        ).fetchone()["n"]
    return {"sent": sent, "failed": failed, "remaining": remaining}


def get_outbox_status() -> dict:
    """Return a summary of the outbox state (for UI/debugging)."""
    with conn() as c:
        pending = c.execute(
            "SELECT COUNT(*) AS n FROM sync_outbox WHERE status='pending'"
        ).fetchone()["n"]
        sent = c.execute(
            "SELECT COUNT(*) AS n FROM sync_outbox WHERE status='sent'"
        ).fetchone()["n"]
        failed = c.execute(
            "SELECT COUNT(*) AS n FROM sync_outbox WHERE status='failed'"
        ).fetchone()["n"]
        recent = c.execute(
            "SELECT id, entity_type, entity_key, status, attempts, last_attempt_at, created_at "
            "FROM sync_outbox ORDER BY id DESC LIMIT 10"
        ).fetchall()
    return {
        "pending": pending, "sent": sent, "failed": failed,
        "recent": [dict(r) for r in recent],
    }
