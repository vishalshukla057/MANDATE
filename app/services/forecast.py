from datetime import timedelta
from typing import Any, Dict, List
from sqlalchemy.orm import Session

from app.models.invoice import Invoice
from app.models.client import Client
from app.models.payout import Payout
from app.services.clock import ClockService
from app.services.risk import RiskScoringService


class CashFlowForecastService:
    @classmethod
    def generate_forecast(cls, db: Session, days: int = 30) -> Dict[str, Any]:
        """
        Generates probability-weighted cash flow forecast for N days.
        Weights expected collections by each client's historical reliability,
        deducts pending payouts as scheduled outflows, and computes confidence bands.
        """
        now_dt = ClockService.now()
        horizon_dt = now_dt + timedelta(days=days)

        open_invoices = (
            db.query(Invoice)
            .filter(Invoice.status.in_(["sent", "viewed", "partially_paid", "overdue"]))
            .filter(Invoice.amount_due > 0.0)
            .all()
        )

        pending_payouts = (
            db.query(Payout)
            .filter(Payout.status.in_(["proposed", "pending_approval", "approved"]))
            .all()
        )

        weekly_buckets: List[Dict[str, Any]] = []
        num_weeks = max(1, (days + 6) // 7)

        for w in range(num_weeks):
            start_week = now_dt + timedelta(days=w * 7)
            end_week = now_dt + timedelta(days=(w + 1) * 7)
            weekly_buckets.append({
                "week": w + 1,
                "label": f"Week {w + 1} ({start_week.strftime('%b %d')} - {end_week.strftime('%b %d')})",
                "expected_inflows": 0.0,
                "pessimistic_inflows": 0.0,
                "optimistic_inflows": 0.0,
                "scheduled_outflows": 0.0,
                "projected_net": 0.0,
            })

        total_expected_inflow = 0.0
        total_pessimistic_inflow = 0.0
        total_optimistic_inflow = 0.0

        for inv in open_invoices:
            client = db.query(Client).filter(Client.id == inv.client_id).first()
            risk_label = client.risk_label if client else "reliable"

            # Historical probability
            if risk_label == "reliable":
                prob = 0.95
                pessimistic_factor = 0.85
                delay_days = 2.0
            elif risk_label == "watch":
                prob = 0.70
                pessimistic_factor = 0.50
                delay_days = 8.0
            else:  # risky
                prob = 0.45
                pessimistic_factor = 0.25
                delay_days = 18.0

            # Projected payment date = due_date + client historical delay
            from datetime import timezone
            inv_due = inv.due_date
            if inv_due.tzinfo is None:
                inv_due = inv_due.replace(tzinfo=timezone.utc)
            if now_dt.tzinfo is None:
                now_dt = now_dt.replace(tzinfo=timezone.utc)

            projected_pay_dt = max(now_dt, inv_due + timedelta(days=delay_days))
            diff_days = (projected_pay_dt - now_dt).total_seconds() / 86400.0

            week_idx = int(diff_days // 7)
            if 0 <= week_idx < len(weekly_buckets):
                expected_val = round(inv.amount_due * prob, 2)
                pessimistic_val = round(inv.amount_due * pessimistic_factor, 2)
                optimistic_val = round(inv.amount_due * 1.0, 2)

                weekly_buckets[week_idx]["expected_inflows"] += expected_val
                weekly_buckets[week_idx]["pessimistic_inflows"] += pessimistic_val
                weekly_buckets[week_idx]["optimistic_inflows"] += optimistic_val

                total_expected_inflow += expected_val
                total_pessimistic_inflow += pessimistic_val
                total_optimistic_inflow += optimistic_val

        # Distribute pending payouts (outflows) into week 1 and 2
        total_outflows = 0.0
        for p in pending_payouts:
            weekly_buckets[0]["scheduled_outflows"] += p.amount
            total_outflows += p.amount

        # Calculate projected net per bucket
        for b in weekly_buckets:
            b["expected_inflows"] = round(b["expected_inflows"], 2)
            b["pessimistic_inflows"] = round(b["pessimistic_inflows"], 2)
            b["optimistic_inflows"] = round(b["optimistic_inflows"], 2)
            b["scheduled_outflows"] = round(b["scheduled_outflows"], 2)
            b["projected_net"] = round(b["expected_inflows"] - b["scheduled_outflows"], 2)

        return {
            "forecast_horizon_days": days,
            "currency": "USD",
            "summary": {
                "total_expected_inflow": round(total_expected_inflow, 2),
                "total_pessimistic_inflow": round(total_pessimistic_inflow, 2),
                "total_optimistic_inflow": round(total_optimistic_inflow, 2),
                "total_scheduled_outflow": round(total_outflows, 2),
                "projected_net_cash_flow": round(total_expected_inflow - total_outflows, 2),
            },
            "weekly_timeline": weekly_buckets,
        }

