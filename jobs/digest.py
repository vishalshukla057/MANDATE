import logging
from sqlalchemy.orm import Session
from app.db import SessionLocal
from app.models.invoice import Invoice
from app.models.job import JobLog
from app.services.clock import ClockService
from app.services.forecast import CashFlowForecastService
from app.services.feed import feed_broker

logger = logging.getLogger("digest_job")


async def run_daily_digest() -> dict:
    """Generates daily morning collection digest and broadcasts via SSE feed."""
    db = SessionLocal()
    started_at = ClockService.now()
    job_log = JobLog(job="digest", started_at=started_at)
    db.add(job_log)
    db.commit()

    try:
        overdue_invoices = db.query(Invoice).filter(Invoice.status == "overdue").all()
        forecast = CashFlowForecastService.generate_forecast(db, days=7)
        expected_7d = forecast["weekly_timeline"][0]["expected_inflows"] if forecast["weekly_timeline"] else 0.0

        digest_text = (
            f"Daily Digest: {len(overdue_invoices)} invoices currently overdue totaling "
            f"${sum(i.amount_due for i in overdue_invoices):.2f}. "
            f"${expected_7d:.2f} projected collections expected over the next 7 days."
        )

        job_log.finished_at = ClockService.now()
        job_log.result = {
            "overdue_count": len(overdue_invoices),
            "expected_this_week": expected_7d,
            "digest": digest_text,
        }
        db.commit()

        feed_broker.publish("daily_digest", {"digest": digest_text, "overdue_count": len(overdue_invoices)})
        return job_log.result
    except Exception as exc:
        job_log.finished_at = ClockService.now()
        job_log.error = str(exc)
        db.commit()
        return {"error": str(exc)}
    finally:
        db.close()
