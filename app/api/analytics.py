from datetime import timedelta, timezone
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.auth_context import get_tenant_id
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
def get_analytics_summary(tenant_id: str = Depends(get_tenant_id), db: Session = Depends(get_db)):
    """Executive financial summary: total outstanding, overdue, DSO, collected, and open risk."""
    now_dt = ClockService.now()
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=timezone.utc)

    invoices = db.query(Invoice).filter(Invoice.tenant_id == tenant_id).all()
    payments = db.query(Payment).filter(Payment.tenant_id == tenant_id).all()

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
    at_risk_count = db.query(Invoice).filter(Invoice.tenant_id == tenant_id, Invoice.status.in_(["overdue", "disputed"])).count()

    # Top late clients
    late_clients = (
        db.query(Client)
        .filter(Client.tenant_id == tenant_id, Client.late_count > 0)
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
def get_cashflow_forecast(days: int = Query(30, ge=7, le=90), tenant_id: str = Depends(get_tenant_id), db: Session = Depends(get_db)):
    """Expected collections weighted by client historical probability, with scheduled outflows and confidence bands."""
    return CashFlowForecastService.generate_forecast(db, days=days, tenant_id=tenant_id)


@router.get("/aging")
def get_aging_buckets(tenant_id: str = Depends(get_tenant_id), db: Session = Depends(get_db)):
    """Aging buckets: 0-30, 31-60, 61-90, 90+ days overdue."""
    now_dt = ClockService.now()
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=timezone.utc)

    overdue_invoices = db.query(Invoice).filter(Invoice.tenant_id == tenant_id, Invoice.status == "overdue").all()

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
def get_agent_observability_stats(tenant_id: str = Depends(get_tenant_id), db: Session = Depends(get_db)):
    """Observability metrics: tool executions, auto-executed vs approved vs rejected vs blocked."""
    actions = db.query(AgentAction).filter(AgentAction.tenant_id == tenant_id).all()
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
async def ask_analytics_question(payload: AskRequest, tenant_id: str = Depends(get_tenant_id), db: Session = Depends(get_db)):
    """Translates NL query into a constrained query plan and returns structured chart data + prose."""
    return await AnalyticsQAEngine.answer_question(db, payload.question, tenant_id=tenant_id)


@router.get("/runway-simulator")
def simulate_cashflow_runway(
    monthly_burn_rate: float = Query(12000.0, ge=1000.0, description="Monthly fixed business burn rate"),
    client_delay_days: int = Query(14, ge=0, le=60, description="Simulated additional payment delay across overdue clients"),
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_db)
):
    """Predictive DSO & Cashflow Runway Simulator: evaluates solvency under client delay scenarios."""
    invoices = db.query(Invoice).filter(Invoice.tenant_id == tenant_id).all()
    clients = db.query(Client).filter(Client.tenant_id == tenant_id).all()
    payments = db.query(Payment).filter(Payment.tenant_id == tenant_id).all()

    total_sales = sum(i.total for i in invoices)
    total_outstanding = sum(i.amount_due for i in invoices if i.amount_due > 0.0)
    current_cash_collected = sum(p.amount for p in payments)

    # Baseline DSO
    baseline_dso = round((total_outstanding / max(1.0, total_sales)) * 90, 1)

    # Stressed DSO with added delay
    stressed_outstanding = total_outstanding * (1.0 + (client_delay_days / 90.0))
    stressed_dso = round((stressed_outstanding / max(1.0, total_sales)) * 90, 1)

    daily_burn = monthly_burn_rate / 30.0
    baseline_runway_days = round(current_cash_collected / max(1.0, daily_burn), 1)

    # Calculate individual client default probabilities
    client_risk_breakdown = []
    for c in clients:
        # Risk score 0-100 to default probability
        late_prob = min(0.95, round((c.risk_score * 0.7 + c.late_count * 5.0) / 100.0, 2))
        client_risk_breakdown.append({
            "client_id": c.id,
            "client_name": c.name,
            "risk_score": c.risk_score,
            "late_count": c.late_count,
            "projected_default_probability": late_prob,
            "recommended_action": "Offer 10% early-pay discount" if late_prob > 0.4 else "Standard tone ladder",
        })

    recommendations = []
    if stressed_dso > 45.0:
        recommendations.append("DSO stress exceeds 45-day threshold. Activate automated 5% early-pay discount ladder on high-value accounts.")
    if baseline_runway_days < 60:
        recommendations.append("Cash runway under 60 days. Stage contractor split disbursements into 2 bi-weekly tranches.")

    return {
        "monthly_burn_rate": monthly_burn_rate,
        "simulated_client_delay_days": client_delay_days,
        "baseline_dso_days": baseline_dso,
        "stressed_dso_days": stressed_dso,
        "dso_delta_days": round(stressed_dso - baseline_dso, 1),
        "baseline_runway_days": baseline_runway_days,
        "client_risk_forecast": client_risk_breakdown,
        "proactive_recommendations": recommendations,
    }


