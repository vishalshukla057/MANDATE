import logging
from sqlalchemy.orm import Session
from app.db import SessionLocal
from app.models.invoice import Invoice
from app.models.job import JobLog
from app.services.clock import ClockService
from app.services.reconcile import ReconciliationService
from app.paypal.client import get_paypal_gateway, PayPalError

logger = logging.getLogger("reconcile_poll_job")


async def run_reconcile_poll() -> dict:
    """
    Polls open invoices from PayPal to detect viewed, paid, or partial payment drift,
    serving as the safety net alongside push webhooks.
    """
    db = SessionLocal()
    started_at = ClockService.now()
    job_log = JobLog(job="reconcile_poll", started_at=started_at)
    db.add(job_log)
    db.commit()

    gateway = get_paypal_gateway()
    updated_count = 0
    errors = []

    try:
        # Find open invoices: sent, viewed, partially_paid, overdue
        open_invoices = (
            db.query(Invoice)
            .filter(Invoice.status.in_(["sent", "viewed", "partially_paid", "overdue"]))
            .filter(Invoice.paypal_invoice_id.isnot(None))
            .all()
        )

        for inv in open_invoices:
            try:
                remote_inv = await gateway.get_invoice(inv.paypal_invoice_id)
                remote_status = remote_inv.get("status", "").lower()

                # Detect status changes
                if remote_status == "paid" and inv.status != "paid":
                    inv.status = "paid"
                    inv.amount_due = 0.0
                    inv.amount_paid = inv.total
                    updated_count += 1
                elif remote_status == "marked_as_paid" and inv.status != "paid":
                    inv.status = "paid"
                    inv.amount_due = 0.0
                    inv.amount_paid = inv.total
                    updated_count += 1
                elif remote_status == "cancelled" and inv.status != "cancelled":
                    inv.status = "cancelled"
                    updated_count += 1
                elif remote_status == "refunded" and inv.status != "refunded":
                    inv.status = "refunded"
                    updated_count += 1

                db.commit()
            except PayPalError as pe:
                errors.append(f"Invoice {inv.id}: {pe.message}")
            except Exception as e:
                errors.append(f"Invoice {inv.id}: {str(e)}")

        job_log.finished_at = ClockService.now()
        job_log.result = {
            "polled_count": len(open_invoices),
            "updated_count": updated_count,
            "errors": errors,
        }
        db.commit()
        return job_log.result
    except Exception as exc:
        job_log.finished_at = ClockService.now()
        job_log.error = str(exc)
        db.commit()
        return {"error": str(exc)}
    finally:
        db.close()
