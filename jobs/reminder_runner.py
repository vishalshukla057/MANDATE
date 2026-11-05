import logging
from sqlalchemy.orm import Session
from app.db import SessionLocal
from app.models.invoice import Invoice
from app.models.reminder import Reminder
from app.models.job import JobLog
from app.services.clock import ClockService
from app.agent.tone import ToneEngine
from app.policy.engine import PolicyEngine
from app.paypal.client import get_paypal_gateway, PayPalError

logger = logging.getLogger("reminder_runner")


async def run_reminder_runner() -> dict:
    """
    Finds open overdue invoices eligible for a reminder, drafts tone-adapted message,
    evaluates policy, and sends via PayPal remind endpoint if permitted.
    """
    db = SessionLocal()
    started_at = ClockService.now()
    job_log = JobLog(job="reminder_runner", started_at=started_at)
    db.add(job_log)
    db.commit()

    gateway = get_paypal_gateway()
    sent_count = 0
    queued_count = 0
    blocked_count = 0

    try:
        overdue_invoices = db.query(Invoice).filter(Invoice.status == "overdue").all()
        for inv in overdue_invoices:
            # Compose message and tone
            comp = await ToneEngine.compose_reminder_message(db, inv)
            args = {
                "invoice_id": inv.id,
                "tone": comp["tone"],
                "subject": comp["subject"],
                "body": comp["body"],
                "reasoning": comp["reasoning"],
            }

            policy_decision = PolicyEngine.evaluate(db, "send_reminder", args, tenant_id=inv.tenant_id)
            if policy_decision.is_blocked():
                blocked_count += 1
                continue

            if policy_decision.requires_approval():
                # Add to reminders in proposed status
                rem = Reminder(
                    tenant_id=inv.tenant_id,
                    invoice_id=inv.id,
                    scheduled_for=ClockService.now(),
                    tone=comp["tone"],
                    subject=comp["subject"],
                    body=comp["body"],
                    reasoning=comp["reasoning"],
                    status="proposed",
                )
                db.add(rem)
                queued_count += 1
            else:
                # Dispatch immediately
                try:
                    await gateway.send_reminder(inv.paypal_invoice_id, subject=comp["subject"], note=comp["body"])
                    inv.last_reminder_at = ClockService.now()
                    inv.reminder_count += 1
                    rem = Reminder(
                        tenant_id=inv.tenant_id,
                        invoice_id=inv.id,
                        sent_at=ClockService.now(),
                        tone=comp["tone"],
                        subject=comp["subject"],
                        body=comp["body"],
                        reasoning=comp["reasoning"],
                        status="sent",
                    )
                    db.add(rem)
                    sent_count += 1
                except Exception as e:
                    logger.warning(f"Failed to dispatch reminder for invoice {inv.id}: {e}")

        db.commit()
        job_log.finished_at = ClockService.now()
        job_log.result = {
            "eligible": len(overdue_invoices),
            "sent": sent_count,
            "queued": queued_count,
            "blocked": blocked_count,
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

