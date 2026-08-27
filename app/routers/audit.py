"""v8.2 Phase 1-3 — AI Auditor router.

Endpoints:
- GET  /api/audit/runs — list recent audit runs
- GET  /api/audit/runs/{id} — get a run + its findings
- GET  /api/audit/latest — get the most recent run
- POST /api/audit/run — trigger a manual audit run
- POST /api/audit/findings/{id}/acknowledge — acknowledge a finding
- GET  /api/audit/safe-withdrawal — get the safe withdrawal amount for this month
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Any, Optional
from .. import db
from ..auditor import (
    run_audit, list_audit_runs, get_audit_run, get_latest_audit_run,
    acknowledge_finding, get_safe_withdrawal_amount,
)

router = APIRouter()


@router.get("/api/audit/runs")
def list_runs(limit: int = 20) -> Any:
    """List recent audit runs."""
    return {"runs": list_audit_runs(limit), "count": len(list_audit_runs(limit))}


@router.get("/api/audit/runs/{run_id}")
def get_run(run_id: int) -> Any:
    """Get a single audit run + its findings (severity-ranked)."""
    result = get_audit_run(run_id)
    if not result:
        raise HTTPException(404, "Audit run not found")
    return result


@router.get("/api/audit/latest")
def get_latest() -> Any:
    """Get the most recent audit run + its findings."""
    result = get_latest_audit_run()
    if not result:
        return {"run": None, "findings": [], "note": "No audit runs yet. Run an audit to get started."}
    return result


@router.post("/api/audit/run")
def trigger_run(trigger: str = "manual", period: str = "") -> Any:
    """Trigger an audit run. Returns the run + findings.

    v8.4: Deduplicates pending_actions — before inserting new audit findings,
    expires old pending audit actions for the same check_key to prevent duplicates.
    """
    if trigger not in ("manual", "month_end"):
        raise HTTPException(400, "trigger must be 'manual' or 'month_end'")
    result = run_audit(trigger=trigger, period=period)
    # Create pending_actions for actionable critical findings
    actionable_keys = {"over_withdrawal", "negative_stock", "stock_reserve_days"}
    import json
    with db.conn() as c:
        for finding in result["findings"]:
            if finding["severity"] == "critical" and finding["check_key"] in actionable_keys:
                # v8.4: Expire old pending actions for the same check_key before inserting
                c.execute(
                    "UPDATE pending_actions SET status='expired' "
                    "WHERE source='ai_auditor' AND status='pending' "
                    "AND json_extract(payload_json, '$.check_key')=?",
                    (finding["check_key"],)
                )
                c.execute(
                    "INSERT INTO pending_actions(action_type, payload_json, reason, "
                    "impact_summary, source, automation_level) VALUES(?,?,?,?,?,2)",
                    ("audit_finding", json.dumps({
                        "finding_id": None,
                        "check_key": finding["check_key"],
                        "title": finding["title"],
                        "amount": finding["amount"],
                    }), finding["title"], finding["detail"][:200],
                     "ai_auditor")
                )
    return result


class AckIn(BaseModel):
    reason: str = ""


@router.post("/api/audit/findings/{finding_id}/acknowledge")
def ack_finding(finding_id: int, payload: AckIn) -> Any:
    """Acknowledge a finding (mark as acknowledged, optional reason)."""
    ok = acknowledge_finding(finding_id, payload.reason)
    if not ok:
        raise HTTPException(404, "Finding not found or already acknowledged")
    return {"ok": True}


@router.get("/api/audit/safe-withdrawal")
def safe_withdrawal() -> Any:
    """Get the safe withdrawal amount for the current month."""
    return get_safe_withdrawal_amount()
