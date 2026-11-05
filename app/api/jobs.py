from typing import Any, Dict, List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models.job import JobLog
from jobs.overdue_scan import run_overdue_scan
from jobs.reminder_runner import run_reminder_runner
from jobs.reconcile_poll import run_reconcile_poll
from jobs.digest import run_daily_digest
from app.services.risk import RiskScoringService

router = APIRouter(prefix="/jobs", tags=["Jobs & Scheduler"])

JOB_HANDLERS = {
    "overdue_scan": run_overdue_scan,
    "reminder_runner": run_reminder_runner,
    "reconcile_poll": run_reconcile_poll,
    "digest": run_daily_digest,
}


@router.get("")
def list_job_runs(limit: int = 50, db: Session = Depends(get_db)):
    """List recent executions from jobs_log."""
    logs = db.query(JobLog).order_by(JobLog.started_at.desc()).limit(limit).all()
    return logs


@router.post("/{name}/run")
async def trigger_job_manually(name: str, db: Session = Depends(get_db)):
    """Manually run a background job for demo or immediate execution."""
    if name == "risk_recompute":
        count = RiskScoringService.recompute_all_clients(db)
        return {"job": name, "result": {"recomputed_clients": count}}

    handler = JOB_HANDLERS.get(name)
    if not handler:
        raise HTTPException(
            status_code=404,
            detail=f"Job '{name}' not found. Available jobs: {list(JOB_HANDLERS.keys()) + ['risk_recompute']}",
        )

    result = await handler()
    return {"job": name, "result": result}

