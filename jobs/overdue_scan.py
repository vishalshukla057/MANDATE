import logging
from sqlalchemy.orm import Session
from app.db import SessionLocal
from app.models.invoice import Invoice
from app.models.job import JobLog
from app.services.clock import ClockService
from app.services.audit import AuditService

logger = logging.getLogger("overdue_scan")


async def run_overdue_scan() -> dict:
    """
    Finds invoices whose due_date has passed according to ClockService.now(),
    and marks them 'overdue'.
    """
    db = SessionLocal()
    started_at = ClockService.now()
    job_log = JobLog(job="overdue_scan", started_at=started_at)
    db.add(job_log)
    db.commit()

    now_dt = ClockService.now()
    marked_overdue = 0

    try:
        candidate_invoices = (
            db.query(Invoice)
            .filter(Invoice.status.in_(["sent", "viewed", "partially_paid"]))
            .filter(Invoice.due_date < now_dt)
            .filter(Invoice.amount_due > 0.0)
            .all()
        )

        for inv in candidate_invoices:
            inv_due = inv.due_date
            from datetime import timezone
            if inv_due and inv_due.tzinfo is None:
                inv_due = inv_due.replace(tzinfo=timezone.utc)
            if now_dt.tzinfo is None:
                now_dt = now_dt.replace(tzinfo=timezone.utc)
            inv.status = "overdue"
            marked_overdue += 1
            AuditService.record(
                db=db,
                actor="system",
                event="invoice_marked_overdue",
                entity_type="invoice",
                entity_id=inv.id,
                payload={"days_overdue": (now_dt - inv_due).total_seconds() / 86400.0, "amount_due": inv.amount_due},
            )

        db.commit()
        job_log.finished_at = ClockService.now()
        job_log.result = {"marked_overdue": marked_overdue}
        db.commit()
        return job_log.result
    except Exception as exc:
        job_log.finished_at = ClockService.now()
        job_log.error = str(exc)
        db.commit()
        return {"error": str(exc)}
    finally:
        db.close()
