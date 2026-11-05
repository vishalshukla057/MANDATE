import json
import logging
from typing import Any, Dict
from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db, SessionLocal
from app.models.webhook import WebhookEvent
from app.models.invoice import Invoice
from app.models.dispute import Dispute
from app.services.reconcile import ReconciliationService
from app.services.audit import AuditService
from app.services.clock import ClockService
from app.paypal.client import get_paypal_gateway

logger = logging.getLogger("webhooks")
router = APIRouter(prefix="/webhooks", tags=["Webhooks"])


def process_webhook_event_async(event_id: str):
    """Asynchronous background worker for processing received and verified webhooks."""
    db = SessionLocal()
    try:
        ev = db.query(WebhookEvent).filter(WebhookEvent.id == event_id).first()
        if not ev or ev.status == "processed":
            return

        event_type = ev.event_type
        resource = ev.payload.get("resource", {})

        logger.info(f"Processing webhook event: {event_type} (ID: {ev.paypal_event_id})")

        # 1. Invoice Paid
        if event_type == "INVOICING.INVOICE.PAID":
            paypal_inv_id = resource.get("id") or resource.get("invoice", {}).get("id")
            payments_list = resource.get("payments", {}).get("paid_amount", {})
            amount = float(payments_list.get("value", 0.0)) if payments_list else 0.0
            currency = payments_list.get("currency_code", "USD") if payments_list else "USD"

            # If amount is 0, check invoice detail
            if amount == 0.0:
                amount = float(resource.get("amount", {}).get("value", 0.0))

            txn_id = f"TXN-{ev.paypal_event_id}"
            inv, payment = ReconciliationService.reconcile_payment(
                db=db,
                paypal_transaction_id=txn_id,
                amount=amount,
                currency=currency,
                paypal_invoice_id=paypal_inv_id,
                raw_event_id=ev.paypal_event_id,
            )
            if inv and inv.status == "paid":
                import asyncio
                from app.services.payout_service import PayoutService
                try:
                    asyncio.run(PayoutService.process_invoice_paid_payouts(db, inv))
                except Exception as p_err:
                    logger.warning(f"Error evaluating payout rules: {p_err}")

        # 2. Invoice Cancelled
        elif event_type == "INVOICING.INVOICE.CANCELLED":
            paypal_inv_id = resource.get("id")
            invoice = db.query(Invoice).filter(Invoice.paypal_invoice_id == paypal_inv_id).first()
            if invoice:
                invoice.status = "cancelled"
                invoice.amount_due = 0.0
                db.commit()

        # 3. Invoice Refunded
        elif event_type in ["INVOICING.INVOICE.REFUNDED", "PAYMENT.CAPTURE.REFUNDED"]:
            paypal_inv_id = resource.get("id") or resource.get("invoice_id")
            refund_amount = float(resource.get("amount", {}).get("value", 0.0))
            currency = resource.get("amount", {}).get("currency_code", "USD")
            ReconciliationService.reconcile_refund(
                db=db,
                refund_id=ev.paypal_event_id,
                amount=refund_amount,
                currency=currency,
                paypal_invoice_id=paypal_inv_id,
            )

        # 4. Dispute Created
        elif event_type == "CUSTOMER.DISPUTE.CREATED":
            dispute_id = resource.get("dispute_id", f"DISP-{ev.paypal_event_id}")
            existing_dispute = db.query(Dispute).filter(Dispute.paypal_dispute_id == dispute_id).first()
            if not existing_dispute:
                amount_info = resource.get("dispute_amount", {})
                amount = float(amount_info.get("value", 0.0)) if amount_info else 0.0
                dispute = Dispute(
                    tenant_id=settings.DEFAULT_TENANT_ID,
                    paypal_dispute_id=dispute_id,
                    transaction_id=resource.get("disputed_transactions", [{}])[0].get("seller_transaction_id"),
                    reason=resource.get("reason", "MERCHANDISE_OR_SERVICE_NOT_RECEIVED"),
                    status=resource.get("status", "UNDER_REVIEW"),
                    amount=amount,
                    due_by=ClockService.now(),
                    evidence_pack={"webhook_resource": resource},
                )
                db.add(dispute)
                db.commit()

        ev.status = "processed"
        ev.processed_at = ClockService.now()
        db.commit()

    except Exception as exc:
        logger.error(f"Error processing webhook event {event_id}: {exc}", exc_info=True)
        if ev:
            ev.status = "failed"
            ev.error = str(exc)
            db.commit()
    finally:
        db.close()


@router.post("/paypal", status_code=status.HTTP_200_OK)
async def paypal_webhook_handler(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """
    Public webhook receiver for PayPal events.
    Verifies headers and signature, deduplicates by event ID, stores, and delegates to background queue.
    """
    raw_body = await request.body()
    body_str = raw_body.decode("utf-8")
    
    try:
        payload = json.loads(body_str)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Malformed JSON payload")

    event_id = payload.get("id")
    event_type = payload.get("event_type", "UNKNOWN")

    if not event_id:
        raise HTTPException(status_code=400, detail="Missing event id in webhook payload")

    # Step 3: Deduplication check
    existing = db.query(WebhookEvent).filter(WebhookEvent.paypal_event_id == event_id).first()
    if existing:
        logger.info(f"Duplicate webhook received for event {event_id}. Idempotent return 200.")
        return {"status": "already_received", "event_id": event_id}

    # Step 2: Signature verification
    headers = dict(request.headers)
    gateway = get_paypal_gateway()
    
    verified = True
    if settings.PAYPAL_MODE.lower() == "live":
        try:
            verified = await gateway.verify_webhook_signature(
                headers=headers,
                raw_body=body_str,
                webhook_id=settings.PAYPAL_WEBHOOK_ID or "",
            )
        except Exception as exc:
            logger.error(f"Signature verification request failed: {exc}")
            verified = False

        if not verified:
            # Store unverified event for debugging, reject with 400
            failed_ev = WebhookEvent(
                tenant_id=settings.DEFAULT_TENANT_ID,
                paypal_event_id=event_id,
                event_type=event_type,
                payload=payload,
                verified=False,
                status="rejected_signature",
            )
            db.add(failed_ev)
            db.commit()
            raise HTTPException(status_code=400, detail="Webhook signature verification failed")

    # Step 4: Persist event and dispatch background job
    webhook_ev = WebhookEvent(
        tenant_id=settings.DEFAULT_TENANT_ID,
        paypal_event_id=event_id,
        event_type=event_type,
        payload=payload,
        verified=verified,
        status="received",
    )
    db.add(webhook_ev)
    db.commit()
    db.refresh(webhook_ev)

    AuditService.record(
        db=db,
        actor="paypal",
        event="webhook_received",
        entity_type="webhook",
        entity_id=event_id,
        payload={"event_type": event_type, "verified": verified},
    )

    background_tasks.add_task(process_webhook_event_async, webhook_ev.id)
    return {"status": "accepted", "event_id": event_id}
