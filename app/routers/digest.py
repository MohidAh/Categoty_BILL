"""Owner digest (Twilio WhatsApp) API endpoints."""
from __future__ import annotations
from typing import Any
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import digest

router = APIRouter()


class DigestConfigIn(BaseModel):
    enabled: bool | None = None
    hour: int | None = None              # 0-23, PKT
    phone: str | None = None             # E.164 like +923331234567
    twilio_sid: str | None = None
    twilio_token: str | None = None      # write-only — encrypted before persist
    whatsapp_from: str | None = None     # 'whatsapp:+1415...'


@router.get("/api/digest/config")
def get_digest_config() -> Any:
    return digest.get_config()


@router.post("/api/digest/config")
def update_digest_config(payload: DigestConfigIn) -> Any:
    digest.update_config(payload.model_dump(exclude_none=True))
    return {"ok": True}


@router.post("/api/digest/preview")
def preview_digest() -> Any:
    """Returns the message that WOULD be sent right now — useful for the
    Settings UI to show the user what they'll get."""
    return {"message": digest.build_digest_message(today_only=True)}


@router.post("/api/digest/test-send")
def test_send_digest() -> Any:
    """Actually send the current digest to the configured phone — for the
    'Test send' button in the Settings UI. Force-sends even if disabled."""
    return digest.send_daily_digest(force=True)
