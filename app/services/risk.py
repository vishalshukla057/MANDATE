from typing import Any, Dict, List, Optional
from sqlalchemy.orm import Session

from app.models.client import Client
from app.models.invoice import Invoice
from app.models.dispute import Dispute
from app.models.payment import Payment
from app.services.clock import ClockService
from app.services.audit import AuditService


class RiskScoringService:
    @classmethod
    def calculate_client_risk(cls, db: Session, client: Client) -> Dict[str, Any]:
        """
        Calculates client risk score (0-100) based on:
        - Late payment frequency (late_count / total)
        - Average days to pay
        - Unpaid overdue ratio
        - Prior dispute history
        Returns score, label ('reliable' | 'watch' | 'risky'), and explainable factors.
        """
        invoices = db.query(Invoice).filter(Invoice.client_id == client.id).all()
        disputes = db.query(Dispute).join(Invoice, Dispute.invoice_id == Invoice.id).filter(Invoice.client_id == client.id).all()

        total_invoices = len(invoices)
        if total_invoices == 0:
            return {
                "score": 15.0,
                "label": "reliable",
                "factors": ["New client with no invoice history. Default moderate trust assigned."],
                "signals": {"total_invoices": 0},
            }

        score = 0.0
        factors = []
        signals = {
            "total_invoices": total_invoices,
            "late_count": client.late_count,
            "paid_count": client.paid_count,
            "avg_days_to_pay": client.avg_days_to_pay,
            "dispute_count": len(disputes),
        }

        # 1. Late payment ratio (up to 40 points)
        late_ratio = client.late_count / max(1, total_invoices)
        signals["late_ratio"] = round(late_ratio, 2)
        if late_ratio > 0.5:
            score += 40.0
            factors.append(f"High late payment history ({late_ratio * 100:.0f}% of past invoices late).")
        elif late_ratio > 0.2:
            score += 20.0
            factors.append(f"Occasional late payments ({late_ratio * 100:.0f}% of invoices delayed).")
        else:
            factors.append("Consistent on-time payment track record.")

        # 2. Average payment delay (up to 30 points)
        if client.avg_days_to_pay > 30:
            score += 30.0
            factors.append(f"Severe payment delay: Average time to pay is {client.avg_days_to_pay:.1f} days.")
        elif client.avg_days_to_pay > 15:
            score += 15.0
            factors.append(f"Moderate payment delay: Average time to pay is {client.avg_days_to_pay:.1f} days.")

        # 3. Open disputes (up to 25 points)
        if len(disputes) > 0:
            score += min(25.0, len(disputes) * 15.0)
            factors.append(f"Client has {len(disputes)} recorded payment dispute(s).")

        # 4. Currently overdue invoices
        overdue_invoices = [i for i in invoices if i.status == "overdue"]
        if overdue_invoices:
            score += min(15.0, len(overdue_invoices) * 7.5)
            factors.append(f"Currently has {len(overdue_invoices)} open overdue invoice(s).")

        final_score = min(100.0, max(0.0, round(score, 1)))

        if final_score <= 25.0:
            label = "reliable"
        elif final_score <= 60.0:
            label = "watch"
        else:
            label = "risky"

        return {
            "score": final_score,
            "label": label,
            "factors": factors,
            "signals": signals,
        }

    @classmethod
    def recompute_and_save(cls, db: Session, client: Client) -> Dict[str, Any]:
        result = cls.calculate_client_risk(db, client)
        client.risk_score = result["score"]
        client.risk_label = result["label"]
        db.commit()
        return result

    @classmethod
    def recompute_all_clients(cls, db: Session) -> int:
        clients = db.query(Client).all()
        for c in clients:
            cls.recompute_and_save(db, c)
        return len(clients)

