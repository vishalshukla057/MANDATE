from datetime import timedelta, timezone
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models.client import Client
from app.models.invoice import Invoice
from app.models.payment import Payment
from app.models.agent import AgentAction, AgentRun
from app.services.clock import ClockService
from app.services.forecast import CashFlowForecastService
from app.agent.qa import AnalyticsQAEngine

router = APIRouter(prefix="/analytics", tags=["Analytics"])


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1)


@router.get("/summary")
def get_analytics_summary(db: Session = Depends(get_db)):
    """Executive financial summary: total outstanding, overdue, DSO, collected, and open risk."""
    now_dt = ClockService.now()
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=timezone.utc)

    invoices = db.query(Invoice).filter(Invoice.tenant_id == settings.DEFAULT_TENANT_ID).all()
    payments = db.query(Payment).filter(Payment.tenant_id == settings.DEFAULT_TENANT_ID).all()

    total_outstanding = sum(i.amount_due for i in invoices if i.amount_due > 0.0)
    total_overdue = sum(i.amount_due for i in invoices if i.status == "overdue")

    # Payments this month / week
    start_of_month = now_dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    start_of_week = now_dt - timedelta(days=now_dt.weekday())

    collected_month = sum(
        p.amount for p in payments if (p.paid_at.replace(tzinfo=timezone.utc) if p.paid_at.tzinfo is None else p.paid_at) >= start_of_month
    )
    collected_week = sum(
        p.amount for p in payments if (p.paid_at.replace(tzinfo=timezone.utc) if p.paid_at.tzinfo is None else p.paid_at) >= start_of_week
    )

    # DSO
    total_sales = sum(i.total for i in invoices)
    dso = round((total_outstanding / max(1.0, total_sales)) * 90, 1)

    # Invoices at risk
    at_risk_count = db.query(Invoice).filter(Invoice.status.in_(["overdue", "disputed"])).count()

    # Top late clients
    late_clients = (
        db.query(Client)
        .filter(Client.late_count > 0)
        .order_by(Client.late_count.desc())
        .limit(3)
        .all()
    )

    return {
        "currency": "USD",
        "total_outstanding": round(total_outstanding, 2),
        "total_overdue": round(total_overdue, 2),
        "collected_this_month": round(collected_month, 2),
        "collected_this_week": round(collected_week, 2),
        "dso_days": dso,
        "invoices_at_risk_count": at_risk_count,
        "top_late_clients": [
            {"name": c.name, "late_count": c.late_count, "avg_days_to_pay": c.avg_days_to_pay}
            for c in late_clients
        ],
    }


@router.get("/cashflow")
def get_cashflow_forecast(days: int = Query(30, ge=7, le=90), db: Session = Depends(get_db)):
    """Expected collections weighted by client historical probability, with scheduled outflows and confidence bands."""
    return CashFlowForecastService.generate_forecast(db, days=days)


@router.get("/aging")
def get_aging_buckets(db: Session = Depends(get_db)):
    """Aging buckets: 0-30, 31-60, 61-90, 90+ days overdue."""
    now_dt = ClockService.now()
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=timezone.utc)

    overdue_invoices = db.query(Invoice).filter(Invoice.status == "overdue").all()

    buckets = {
        "0-30": {"count": 0, "total": 0.0, "items": []},
        "31-60": {"count": 0, "total": 0.0, "items": []},
        "61-90": {"count": 0, "total": 0.0, "items": []},
        "90+": {"count": 0, "total": 0.0, "items": []},
    }

    for inv in overdue_invoices:
        inv_due = inv.due_date
        if inv_due.tzinfo is None:
            inv_due = inv_due.replace(tzinfo=timezone.utc)

        days_late = max(0, int((now_dt - inv_due).total_seconds() // 86400))
        item_summary = {
            "invoice_id": inv.id,
            "invoice_number": inv.paypal_invoice_number,
            "amount_due": inv.amount_due,
            "days_overdue": days_late,
        }

        if days_late <= 30:
            key = "0-30"
        elif days_late <= 60:
            key = "31-60"
        elif days_late <= 90:
            key = "61-90"
        else:
            key = "90+"

        buckets[key]["count"] += 1
        buckets[key]["total"] = round(buckets[key]["total"] + inv.amount_due, 2)
        buckets[key]["items"].append(item_summary)

    return {"aging_buckets": buckets}


@router.get("/agent-stats")
def get_agent_observability_stats(db: Session = Depends(get_db)):
    """Observability metrics: tool executions, auto-executed vs approved vs rejected vs blocked."""
    actions = db.query(AgentAction).all()
    total_actions = len(actions)

    auto_executed = sum(1 for a in actions if a.status == "auto_executed")
    approved = sum(1 for a in actions if a.status == "approved")
    rejected = sum(1 for a in actions if a.status == "rejected")
    blocked = sum(1 for a in actions if a.status == "blocked")
    pending = sum(1 for a in actions if a.status == "pending_approval")

    approval_rate = (approved / max(1, approved + rejected)) * 100.0

    return {
        "total_actions": total_actions,
        "auto_executed": auto_executed,
        "approved": approved,
        "rejected": rejected,
        "blocked": blocked,
        "pending_approval": pending,
        "human_approval_rate_pct": round(approval_rate, 1),
    }


@router.post("/ask")
async def ask_analytics_question(payload: AskRequest, db: Session = Depends(get_db)):
    """Translates NL query into a constrained query plan and returns structured chart data + prose."""
    return await AnalyticsQAEngine.answer_question(db, payload.question)

