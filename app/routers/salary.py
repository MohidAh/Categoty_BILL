"""v8.18.13 — Staff Salary Management API.

Endpoints (all under /api/salary/):
  GET  /api/salary/month?month=YYYY-MM        month sheet (employees+math)
  POST /api/salary/records                    upsert record (off-days etc.)
  POST /api/salary/records/{id}/pay           mark paid (+ cash out)
  DEL  /api/salary/records/{id}               delete record (+expense+cash)
  POST /api/salary/advances                   record an advance (+cash out)
  GET  /api/salary/advances                   list advances
  DEL  /api/salary/advances/{id}              delete advance (+cash)
  GET  /api/salary/history/{employee_id}      per-employee monthly history
  POST /api/salary/employees                  quick-add employee with salary
  PUT  /api/salary/employees/{id}             set fixed monthly salary
"""
from typing import Any, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import db
from .. import salary

router = APIRouter()


class SalaryRecordIn(BaseModel):
    employee_id: int
    month: str
    off_days_taken: int = 0
    allowed_off_days: Optional[int] = None
    notes: str = ""


class PaySalaryIn(BaseModel):
    payment_method: str = "cash"
    date: str = ""


class AdvanceIn(BaseModel):
    employee_id: int
    amount: float
    date: str = ""
    description: str = ""
    payment_method: str = "cash"


class SalaryEmployeeIn(BaseModel):
    name: str
    phone: str = ""
    role: str = "cashier"
    monthly_salary: float = 0


class SalaryEmployeeUpdate(BaseModel):
    monthly_salary: Optional[float] = None
    name: Optional[str] = None
    phone: Optional[str] = None


@router.get("/api/salary/month")
def salary_month_route(month: str = "") -> Any:
    try:
        return salary.get_salary_month(month)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/api/salary/records")
def upsert_salary_record_route(payload: SalaryRecordIn) -> Any:
    try:
        rec = salary.upsert_salary_record(
            payload.employee_id, payload.month, payload.off_days_taken,
            payload.allowed_off_days, payload.notes,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.log_activity(
        "salary_record_saved", "salary_record", rec["id"],
        f"Salary record {payload.month}: {rec.get('final_payable', 0):,.0f} payable "
        f"({rec.get('off_days_taken', 0)} off-days taken)",
        {"employee_id": payload.employee_id, "month": payload.month,
         "off_days_taken": payload.off_days_taken,
         "final_payable": rec.get("final_payable")},
    )
    return rec


@router.post("/api/salary/records/{rid}/pay")
def pay_salary_route(rid: int, payload: PaySalaryIn) -> Any:
    try:
        rec = salary.pay_salary_record(rid, payload.payment_method,
                                       payload.date or None)
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.log_activity(
        "salary_paid", "salary_record", rid,
        f"Salary paid: Rs {rec.get('final_payable', 0):,.0f} ({rec.get('month')})",
        {"record_id": rid, "final_payable": rec.get("final_payable"),
         "payment_method": rec.get("payment_method")},
    )
    return rec


@router.delete("/api/salary/records/{rid}")
def delete_salary_record_route(rid: int) -> Any:
    ok = salary.delete_salary_record(rid)
    if not ok:
        raise HTTPException(404, "salary record not found")
    db.log_activity("salary_record_deleted", "salary_record", rid,
                    f"Deleted salary record #{rid} (expense + cash entries removed)",
                    {"rid": rid})
    return {"ok": True}


@router.post("/api/salary/advances")
def record_advance_route(payload: AdvanceIn) -> Any:
    try:
        aid = salary.record_advance(
            payload.employee_id, payload.amount,
            payload.date or None, payload.description, payload.payment_method,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.log_activity(
        "salary_advance_recorded", "salary_advance", aid,
        f"Salary advance: Rs {payload.amount:,.0f}",
        {"employee_id": payload.employee_id, "amount": payload.amount,
         "date": payload.date},
    )
    return {"id": aid}


@router.get("/api/salary/advances")
def list_advances_route(month: str = "", employee_id: int = None,
                        limit: int = 100) -> Any:
    return {"advances": salary.list_advances(month, employee_id, limit)}


@router.delete("/api/salary/advances/{aid}")
def delete_advance_route(aid: int) -> Any:
    ok = salary.delete_advance(aid)
    if not ok:
        raise HTTPException(404, "advance not found")
    db.log_activity("salary_advance_deleted", "salary_advance", aid,
                    f"Deleted salary advance #{aid}", {"aid": aid})
    return {"ok": True}


@router.get("/api/salary/history/{employee_id}")
def salary_history_route(employee_id: int) -> Any:
    try:
        history = salary.get_salary_history(employee_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return {"history": history}


@router.post("/api/salary/employees")
def add_salary_employee_route(payload: SalaryEmployeeIn) -> Any:
    try:
        eid = salary.add_salary_employee(
            payload.name, payload.phone, payload.role, payload.monthly_salary
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.log_activity(
        "salary_employee_added", "employee", eid,
        f"Employee added with salary: {payload.name} "
        f"(Rs {payload.monthly_salary:,.0f}/month)",
        {"name": payload.name, "monthly_salary": payload.monthly_salary},
    )
    return {"id": eid}


@router.put("/api/salary/employees/{eid}")
def update_salary_employee_route(eid: int, payload: SalaryEmployeeUpdate) -> Any:
    from .. import shop as shop_mod
    if payload.name is not None or payload.phone is not None:
        ok = shop_mod.update_employee(eid, name=payload.name, phone=payload.phone)
        if not ok and payload.monthly_salary is None:
            raise HTTPException(404, "employee not found or no fields to update")
    if payload.monthly_salary is not None:
        try:
            ok = salary.set_employee_salary(eid, payload.monthly_salary)
        except ValueError as e:
            raise HTTPException(400, str(e))
        if not ok:
            raise HTTPException(404, "employee not found")
        db.log_activity(
            "employee_salary_set", "employee", eid,
            f"Monthly salary set to Rs {payload.monthly_salary:,.0f}",
            {"monthly_salary": payload.monthly_salary},
        )
    return {"ok": True}
