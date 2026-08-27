"""FBR POS Integration API endpoints."""
from __future__ import annotations
from typing import Any
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import fbr, security

router = APIRouter()


class FBRCredsIn(BaseModel):
    usr_id: str
    password: str
    pos_id: str
    pos_serial: str = ""
    sandbox: bool = True


@router.get("/api/fbr/status")
def get_fbr_status() -> Any:
    """Return whether FBR is configured + which mode (sandbox/prod)."""
    creds = fbr.get_credentials()
    if not creds:
        return {"configured": False, "sandbox": True}
    return {
        "configured": True,
        "sandbox": creds.get("sandbox", True),
        "pos_id": creds.get("pos_id", ""),
        "usr_id": creds.get("usr_id", ""),
    }


@router.post("/api/fbr/credentials")
def set_fbr_credentials(payload: FBRCredsIn) -> Any:
    """Persist FBR credentials (encrypted at rest via Fernet)."""
    fbr.set_credentials(payload.model_dump())
    return {"ok": True}


@router.delete("/api/fbr/credentials")
def clear_fbr_credentials() -> Any:
    fbr.clear_credentials()
    return {"ok": True}


@router.get("/api/fbr/compliance-check")
def compliance_check() -> Any:
    """Audit FBR readiness across shop profile, credentials, recent sales,
    and receipt template."""
    return fbr.verify_compliance()


@router.post("/api/fbr/post-sale/{sale_id}")
def post_sale(sale_id: int) -> Any:
    """Manually trigger FBR submission for a single sale. The auto path
    calls this on every sale confirmation if 'auto-post' is enabled."""
    result = fbr.post_sale_to_fbr(sale_id)
    if not result["posted"] and result.get("error"):
        # Don't 500 — the call succeeded, FBR rejected it.
        # The UI shows the error inline.
        return result
    return result


class FBRAutoIn(BaseModel):
    enabled: bool


@router.post("/api/fbr/auto-post")
def set_auto_post(payload: FBRAutoIn) -> Any:
    """Toggle whether each new sale is auto-posted to FBR on confirmation.
    Recommend ON for production — keeps you FBR-compliant automatically."""
    from .. import db
    db.set_setting("fbr_auto_post", "1" if payload.enabled else "0")
    return {"ok": True, "enabled": payload.enabled}


@router.get("/api/fbr/auto-post")
def get_auto_post() -> Any:
    from .. import db
    return {"enabled": db.get_setting("fbr_auto_post", "0") == "1"}
