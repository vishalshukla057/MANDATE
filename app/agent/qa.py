import json
import re
from datetime import timedelta, timezone
from typing import Any, Dict, List, Optional
import httpx
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.models.client import Client
from app.models.invoice import Invoice
from app.models.payment import Payment
from app.models.payout import Payout
from app.models.dispute import Dispute
from app.services.clock import ClockService
from app.services.forecast import CashFlowForecastService
from app.config import settings


class QueryPlan(BaseModel):
    intent: str  # "who_owes_money" | "cash_flow" | "dso" | "top_late_clients" | "outstanding_vs_overdue" | "collected" | "payouts"
    filters: Dict[str, Any] = Field(default_factory=dict)
    group_by: Optional[str] = None
    limit: int = 10


class AnalyticsQAEngine:
    @classmethod
    def resolve_intent_heuristically(cls, question: str) -> QueryPlan:
        q = question.lower()
        if "who owes" in q or "owe" in q or "unpaid" in q:
            return QueryPlan(intent="who_owes_money", limit=10)
        elif "cash flow" in q or "forecast" in q or "inflow" in q or "next" in q:
            return QueryPlan(intent="cash_flow", filters={"days": 30})
        elif "dso" in q or "days sales outstanding" in q:
            return QueryPlan(intent="dso")
        elif "late client" in q or "late payers" in q or "chronic" in q:
            return QueryPlan(intent="top_late_clients", limit=5)
        elif "collected" in q or "received" in q:
            return QueryPlan(intent="collected")
        elif "payout" in q or "contractor" in q:
            return QueryPlan(intent="payouts")
        else:
            return QueryPlan(intent="outstanding_vs_overdue")

    @classmethod
    def execute_query_plan(cls, db: Session, plan: QueryPlan, tenant_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Executes constrained queries strictly defined in Python.
        Protects against SQL injection and data leakage.
        """
        tenant = tenant_id or settings.DEFAULT_TENANT_ID
        now_dt = ClockService.now()
        if now_dt.tzinfo is None:
            now_dt = now_dt.replace(tzinfo=timezone.utc)

        intent = plan.intent

        # 1. Who owes me money
        if intent == "who_owes_money":
            invoices = (
                db.query(Invoice)
                .filter(Invoice.tenant_id == tenant)
                .filter(Invoice.amount_due > 0.0)
                .filter(Invoice.status.in_(["sent", "viewed", "partially_paid", "overdue"]))
                .all()
            )
            rows = []
            for inv in invoices:
                client = db.query(Client).filter(Client.id == inv.client_id).first()
                inv_due = inv.due_date
                if inv_due.tzinfo is None:
                    inv_due = inv_due.replace(tzinfo=timezone.utc)
                days_over = max(0, int((now_dt - inv_due).total_seconds() // 86400))
                rows.append({
                    "client": client.name if client else "Unknown",
                    "invoice_number": inv.paypal_invoice_number,
                    "amount_due": inv.amount_due,
                    "status": inv.status,
                    "days_overdue": days_over,
                    "due_date": inv.due_date.strftime("%Y-%m-%d"),
                })
            rows.sort(key=lambda x: (x["amount_due"], x["days_overdue"]), reverse=True)
            return {
                "chart_hint": "table",
                "data": rows[:plan.limit],
                "summary": f"Total outstanding balance across {len(rows)} open invoices is ${sum(r['amount_due'] for r in rows):.2f}.",
            }

        # 2. Cash flow forecast
        elif intent == "cash_flow":
            days = plan.filters.get("days", 30)
            forecast = CashFlowForecastService.generate_forecast(db, days=days, tenant_id=tenant)
            chart_data = [
                {
                    "week": b["label"],
                    "Inflows": b["expected_inflows"],
                    "Outflows": b["scheduled_outflows"],
                    "Net": b["projected_net"],
                }
                for b in forecast["weekly_timeline"]
            ]
            return {
                "chart_hint": "bar",
                "data": chart_data,
                "summary": (
                    f"Projected net cash flow for the next {days} days is ${forecast['summary']['projected_net_cash_flow']:.2f} "
                    f"(${forecast['summary']['total_expected_inflow']:.2f} expected collections minus "
                    f"${forecast['summary']['total_scheduled_outflow']:.2f} contractor outflows)."
                ),
            }

        # 3. DSO (Days Sales Outstanding)
        elif intent == "dso":
            total_invoices = db.query(Invoice).filter(Invoice.tenant_id == tenant).all()
            total_sales = sum(i.total for i in total_invoices)
            total_receivable = sum(i.amount_due for i in total_invoices if i.amount_due > 0)

            # Standard DSO = (Total AR / Total Credit Sales) * 90 days
            dso = round((total_receivable / max(1.0, total_sales)) * 90, 1)
            return {
                "chart_hint": "metric",
                "data": {"dso_days": dso, "total_sales": total_sales, "total_receivable": total_receivable},
                "summary": f"Current Days Sales Outstanding (DSO) is {dso} days. (Industry benchmark is under 45 days).",
            }

        # 4. Top late clients
        elif intent == "top_late_clients":
            clients = db.query(Client).filter(Client.tenant_id == tenant).order_by(Client.late_count.desc(), Client.avg_days_to_pay.desc()).limit(plan.limit).all()
            data = [
                {
                    "client": c.name,
                    "late_invoices": c.late_count,
                    "avg_days_to_pay": c.avg_days_to_pay,
                    "risk_label": c.risk_label,
                    "total_billed": c.total_billed,
                }
                for c in clients
            ]
            return {
                "chart_hint": "bar",
                "data": data,
                "summary": f"Top late payer is {clients[0].name if clients else 'None'} with {clients[0].late_count if clients else 0} delayed payments.",
            }

        # 5. Outstanding vs Overdue
        elif intent == "outstanding_vs_overdue":
            all_open = db.query(Invoice).filter(Invoice.tenant_id == tenant, Invoice.amount_due > 0.0).all()
            total_outstanding = sum(i.amount_due for i in all_open)
            total_overdue = sum(i.amount_due for i in all_open if i.status == "overdue")
            current_not_overdue = total_outstanding - total_overdue

            data = [
                {"category": "Overdue Balance", "amount": round(total_overdue, 2)},
                {"category": "Current Outstanding", "amount": round(current_not_overdue, 2)},
            ]
            return {
                "chart_hint": "pie",
                "data": data,
                "summary": f"Total outstanding is ${total_outstanding:.2f}, of which ${total_overdue:.2f} is currently overdue.",
            }

        # 6. Collected payments
        elif intent == "collected":
            payments = db.query(Payment).filter(Payment.tenant_id == tenant).order_by(Payment.paid_at.desc()).all()
            total_collected = round(sum(p.amount for p in payments), 2)
            rows = []
            for p in payments[:plan.limit]:
                inv = db.query(Invoice).filter(Invoice.id == p.invoice_id).first()
                cli = db.query(Client).filter(Client.id == inv.client_id).first() if inv else None
                rows.append({
                    "client": cli.name if cli else "Client",
                    "invoice": inv.paypal_invoice_number if inv else "-",
                    "amount": p.amount,
                    "method": p.method,
                })
            return {
                "chart_hint": "table",
                "data": rows,
                "summary": f"Total collected is ${total_collected:,.2f} across {len(payments)} payment(s).",
            }

        # 7. Payouts
        elif intent == "payouts":
            payouts = db.query(Payout).filter(Payout.tenant_id == tenant).all()
            total_paid_out = sum(p.amount for p in payouts if p.status in ["submitted", "succeeded"])
            total_pending = sum(p.amount for p in payouts if p.status in ["proposed", "pending_approval", "approved"])

            data = [
                {"status": "Disbursed / Submitted", "amount": round(total_paid_out, 2)},
                {"status": "Pending Human Sign-off", "amount": round(total_pending, 2)},
            ]
            return {
                "chart_hint": "bar",
                "data": data,
                "summary": f"${total_paid_out:.2f} has been disbursed to contractors, with ${total_pending:.2f} awaiting approval.",
            }

        # Default fallback
        return {
            "chart_hint": "metric",
            "data": {},
            "summary": "Financial ledger query processed successfully.",
        }

    @classmethod
    async def answer_question(cls, db: Session, question: str, tenant_id: Optional[str] = None) -> Dict[str, Any]:
        """Translates NL query into constrained query plan and returns structured data + prose summary."""
        plan = cls.resolve_intent_heuristically(question)
        result = cls.execute_query_plan(db, plan, tenant_id=tenant_id)
        answer_text = result["summary"]

        if settings.GEMINI_API_KEY and settings.GEMINI_API_KEY != "mock_gemini_key":
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{settings.GEMINI_MODEL}:generateContent"
                headers = {"Content-Type": "application/json", "x-goog-api-key": settings.GEMINI_API_KEY}
                prompt = (
                    f"User question: {question}\n"
                    f"Computed summary: {result['summary']}\n"
                    f"Ledger data: {json.dumps(result['data'], default=str)}\n\n"
                    "Answer the user's question in 1-3 clear, concise sentences in plain English using ONLY the ledger data above. Do not output JSON or code blocks."
                )
                body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}]}
                async with httpx.AsyncClient(timeout=10.0) as client:
                    res = await client.post(url, headers=headers, json=body)
                    if res.status_code < 400:
                        cands = res.json().get("candidates") or []
                        parts = cands[0].get("content", {}).get("parts", []) if cands else []
                        txt = "".join(p.get("text", "") for p in parts).strip()
                        if txt:
                            answer_text = txt
            except Exception:
                pass

        return {
            "question": question,
            "intent": plan.intent,
            "answer": answer_text,
            "data": result["data"],
            "chart_hint": result["chart_hint"],
        }

