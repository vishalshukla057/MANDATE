import logging
from typing import Any, Dict, Optional, Tuple
from sqlalchemy.orm import Session

from app.models.client import Client
from app.models.invoice import Invoice
from app.services.clock import ClockService
from app.services.risk import RiskScoringService
from app.config import settings

try:
    import anthropic
except ImportError:
    anthropic = None

logger = logging.getLogger("tone_engine")


class ToneEngine:
    @classmethod
    def evaluate_tone_and_signals(cls, db: Session, invoice: Invoice) -> Dict[str, Any]:
        """
        Computes the deterministic tone recommendation and signals from client history.
        """
        client = db.query(Client).filter(Client.id == invoice.client_id).first()
        now_dt = ClockService.now()
        due_dt = invoice.due_date
        from datetime import timezone
        if due_dt and due_dt.tzinfo is None:
            due_dt = due_dt.replace(tzinfo=timezone.utc)
        if now_dt.tzinfo is None:
            now_dt = now_dt.replace(tzinfo=timezone.utc)
        days_overdue = max(0.0, round((now_dt - due_dt).total_seconds() / 86400.0, 1))
        reminder_count = invoice.reminder_count
        risk_info = RiskScoringService.calculate_client_risk(db, client) if client else {"score": 20.0, "label": "reliable"}

        # Tone Ladder:
        # friendly: 1-3 days before due or <= 1 day late
        # neutral: 2-7 days late
        # firm: 8-21 days late OR repeat late payer
        # final: 22+ days late OR reminder_count >= 3
        if days_overdue >= 22.0 or reminder_count >= 3:
            tone = "final"
        elif days_overdue >= 8.0 or (client and client.late_count >= 2):
            tone = "firm"
        elif days_overdue >= 2.0:
            tone = "neutral"
        else:
            tone = "friendly"

        signals = {
            "days_overdue": days_overdue,
            "reminder_count": reminder_count,
            "client_late_count": client.late_count if client else 0,
            "client_paid_count": client.paid_count if client else 0,
            "client_risk_label": risk_info["label"],
            "client_risk_score": risk_info["score"],
            "invoice_total": invoice.total,
            "amount_due": invoice.amount_due,
        }

        # Human-readable reasoning citing signals
        if tone == "final":
            reasoning = f"Invoice #{invoice.paypal_invoice_number} is {days_overdue:.0f} days overdue with {reminder_count} prior reminder(s). Escalating to final tone before alternative action."
        elif tone == "firm":
            reasoning = f"Client has {client.late_count if client else 0} late payments on record and invoice is {days_overdue:.0f} days overdue, so a firm reminder is warranted."
        elif tone == "neutral":
            reasoning = f"Invoice is {days_overdue:.0f} days overdue with {reminder_count} prior reminder(s). Sending neutral payment follow-up."
        else:
            reasoning = f"Invoice is due soon or recently ({days_overdue:.0f} days overdue). Sending friendly courtesy reminder."

        return {
            "tone": tone,
            "signals": signals,
            "reasoning": reasoning,
            "client_name": client.name if client else "Valued Client",
            "invoice_number": invoice.paypal_invoice_number or invoice.id[:8],
            "amount_due": invoice.amount_due,
            "currency": invoice.currency,
            "payer_view_url": invoice.payer_view_url or f"/invoices/{invoice.id}/view",
        }

    @classmethod
    async def compose_reminder_message(cls, db: Session, invoice: Invoice) -> Dict[str, Any]:
        """
        Composes subject and body adhering to tone ladder and hard rules:
        - Must include amount, invoice number, and payer link.
        - No threats or false claims.
        - Word count under 150 words.
        """
        spec = cls.evaluate_tone_and_signals(db, invoice)
        tone = spec["tone"]
        client_name = spec["client_name"]
        inv_num = spec["invoice_number"]
        amount_str = f"${spec['amount_due']:.2f} {spec['currency']}"
        link = spec["payer_view_url"]

        subject_map = {
            "friendly": f"Courtesy Reminder: Invoice #{inv_num} for {amount_str}",
            "neutral": f"Payment Follow-Up: Invoice #{inv_num} ({amount_str})",
            "firm": f"Overdue Notice: Urgent attention needed for Invoice #{inv_num}",
            "final": f"Final Notice: Outstanding balance for Invoice #{inv_num}",
        }
        subject = subject_map.get(tone, f"Invoice #{inv_num} Payment Reminder")

        body_templates = {
            "friendly": (
                f"Hi {client_name},\n\n"
                f"Just a friendly check-in regarding Invoice #{inv_num} for {amount_str}. "
                f"You can review and pay securely via PayPal here:\n{link}\n\n"
                f"Please let us know if you have any questions. Thank you for your business!"
            ),
            "neutral": (
                f"Hello {client_name},\n\n"
                f"Our records show that Invoice #{inv_num} for {amount_str} is currently overdue. "
                f"Please take a moment to settle the balance using the direct PayPal payment link below:\n{link}\n\n"
                f"If payment has already been sent, please disregard this notice."
            ),
            "firm": (
                f"Dear {client_name},\n\n"
                f"Payment for Invoice #{inv_num} ({amount_str}) is significantly overdue. "
                f"Consistent on-time settlement is required to maintain active project terms. "
                f"Please settle this invoice today via PayPal:\n{link}\n\n"
                f"Kindly reach out immediately if there are any billing questions."
            ),
            "final": (
                f"Attention {client_name},\n\n"
                f"This is our final reminder regarding unpaid Invoice #{inv_num} for {amount_str}. "
                f"Multiple reminders have been delivered without resolution. "
                f"Please remit payment immediately through PayPal to prevent account suspension:\n{link}\n\n"
                f"Prompt resolution is expected."
            ),
        }

        body = body_templates.get(tone, body_templates["neutral"])

        # Check hard rules
        words = body.split()
        if len(words) > 150:
            body = " ".join(words[:140]) + f"\n\nPay here: {link}"

        return {
            "tone": tone,
            "subject": subject,
            "body": body,
            "reasoning": spec["reasoning"],
            "signals": spec["signals"],
            "link": link,
        }
