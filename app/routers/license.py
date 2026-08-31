"""License router — status + activation endpoints.

Both endpoints are public BY DESIGN (they must be reachable before login,
because the app is locked until a license is activated). Brute-forcing a
license key is computationally infeasible (Ed25519 signature space) and the
global API throttle still applies.
"""
import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .. import licensing

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/license/status")
def license_status():
    """Current license state + this machine's Setup ID.

    Public: the lock screen, login page and setup wizard all need it to
    route the user (and to display the Setup ID the operator must send to
    the owner to receive a license).
    """
    return licensing.license_state()


class ActivateIn(BaseModel):
    license_key: str


# Human-friendly messages for each rejection reason (code is also returned
# so the frontend can customize further if it wants).
_REASONS = {
    licensing.R_MISSING: "Paste the license key you received.",
    licensing.R_INVALID: "That license key is not valid. Check that you "
                         "copied the whole key (it usually wraps over "
                         "several lines) and try again.",
    licensing.R_MACHINE: "This license was issued for a DIFFERENT machine. "
                         "Send the Setup ID shown below to get a license "
                         "for this computer.",
    licensing.R_EXPIRED: "This license has expired. Send the Setup ID shown "
                         "below to receive a renewal.",
}


@router.post("/api/license/activate")
def license_activate(payload: ActivateIn):
    ok, info, reason = licensing.activate(payload.license_key)
    if not ok:
        return JSONResponse(
            {"error": _REASONS.get(reason, "License activation failed."),
             "code": reason},
            status_code=403,
        )
    state = licensing.license_state()
    return {"ok": True, "license": state["license"]}
