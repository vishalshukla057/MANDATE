import logging
from datetime import datetime
from typing import Any, Dict, Optional, Tuple
from sqlalchemy.orm import Session

from app.models.invoice import Invoice
from app.models.payment import Payment
from app.models.client import Client
from app.services.clock import ClockService
from app.services.audit import AuditService

logger = logging.getLogger("reconciliation")


class ReconciliationService:
    @classmethod
    def reconcile_payment(
        cls,
        db: Session,
        paypal_transaction_id: str,
        amount: float,
        currency: str,
        invoice_id: Optional[str] = None,
        paypal_invoice_id: Optional[str] = None,
        method: str = "PAYPAL",
        raw_event_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ) -> Tuple[Optional[Invoice], Optional[Payment]]:
        """
        Reconciles a payment against an invoice.
        Handles duplicates (idempotency by transaction ID), partial payments, and full payments.
        """
        # 1. Deduplicate payment if already recorded
        existing_payment = (
            db.query(Payment)
            .filter(Payment.paypal_transaction_id == paypal_transaction_id)
            .first()
        )
        if existing_payment:
            logger.info(f"Payment {paypal_transaction_id} already reconciled. Skipping duplicate.")
            return None, existing_payment

        # 2. Match to Invoice
        invoice = None
        if invoice_id:
            invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
        elif paypal_invoice_id:
            invoice = db.query(Invoice).filter(Invoice.paypal_invoice_id == paypal_invoice_id).first()

        if not invoice:
            logger.warning(f"Could not find matching invoice for transaction {paypal_transaction_id}")
            return None, None

        # 3. Create Payment record
        payment = Payment(
            tenant_id=invoice.tenant_id,
            invoice_id=invoice.id,
            paypal_transaction_id=paypal_transaction_id,
            amount=amount,
            currency=currency,
            method=method,
            paid_at=ClockService.now(),
            raw_event_id=raw_event_id,
        )
        db.add(payment)

        # 4. Update Invoice balances
        invoice.amount_paid = round(invoice.amount_paid + amount, 2)
        invoice.amount_due = max(0.0, round(invoice.total - invoice.amount_paid, 2))

        if invoice.amount_due <= 0.001:
            invoice.status = "paid"
        else:
            invoice.status = "partially_paid"

        # 5. Update Client statistics
        client = db.query(Client).filter(Client.id == invoice.client_id).first()
        if client:
            client.total_paid = round(client.total_paid + amount, 2)
            if invoice.status == "paid":
                client.paid_count += 1
                # Update average days to pay
                issue_dt = invoice.issue_date
                now_dt = ClockService.now()
                from datetime import timezone
                if issue_dt and issue_dt.tzinfo is None:
                    issue_dt = issue_dt.replace(tzinfo=timezone.utc)
                if now_dt.tzinfo is None:
                    now_dt = now_dt.replace(tzinfo=timezone.utc)
                days_taken = max(0.0, (now_dt - issue_dt).total_seconds() / 86400.0)
                if client.paid_count == 1:
                    client.avg_days_to_pay = days_taken
                else:
                    client.avg_days_to_pay = round(
                        (client.avg_days_to_pay * (client.paid_count - 1) + days_taken) / client.paid_count,
                        1,
                    )

        db.commit()
        db.refresh(invoice)
        db.refresh(payment)

        # 6. Audit log
        AuditService.record(
            db=db,
            actor="paypal",
            event="payment_reconciled",
            entity_type="invoice",
            entity_id=invoice.id,
            payload={
                "paypal_transaction_id": paypal_transaction_id,
                "amount": amount,
                "currency": currency,
                "invoice_status": invoice.status,
                "amount_due": invoice.amount_due,
            },
            tenant_id=invoice.tenant_id,
        )

        return invoice, payment

    @classmethod
    def reconcile_refund(
        cls,
        db: Session,
        refund_id: str,
        amount: float,
        currency: str,
        invoice_id: Optional[str] = None,
        paypal_invoice_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ) -> Optional[Invoice]:
        """Handles refunds and updates invoice amount_due."""
        invoice = None
        if invoice_id:
            invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
        elif paypal_invoice_id:
            invoice = db.query(Invoice).filter(Invoice.paypal_invoice_id == paypal_invoice_id).first()

        if not invoice:
            return None

        invoice.amount_paid = max(0.0, round(invoice.amount_paid - amount, 2))
        invoice.amount_due = min(invoice.total, round(invoice.total - invoice.amount_paid, 2))

        if invoice.amount_paid <= 0.001:
            invoice.status = "refunded"
        else:
            invoice.status = "partially_paid"

        db.commit()
        db.refresh(invoice)

        AuditService.record(
            db=db,
            actor="paypal",
            event="refund_reconciled",
            entity_type="invoice",
            entity_id=invoice.id,
            payload={"refund_id": refund_id, "amount": amount, "status": invoice.status},
            tenant_id=invoice.tenant_id,
        )

        return invoice
