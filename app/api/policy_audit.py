from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.auth_context import get_tenant_id
from app.models.policy import Policy
from app.models.audit import AuditLog
from app.services.audit import AuditService

router = APIRouter(tags=["Policy & Audit"])


class PolicyUpdateRequest(BaseModel):
    key: str
    value: Any


class KillSwitchRequest(BaseModel):
    active: bool


@router.get("/policy")
def get_policy(tenant_id: str = Depends(get_tenant_id), db: Session = Depends(get_db)):
    """Fetch current policy rules and active overrides."""
    policies = db.query(Policy).filter(Policy.tenant_id == tenant_id).order_by(Policy.version.asc()).all()
    dynamic = {p.key: p.value for p in policies}
    return {
        "KILL_SWITCH": dynamic.get("KILL_SWITCH", settings.KILL_SWITCH),
        "POLICY_AUTO_APPROVE_REMINDERS": dynamic.get("POLICY_AUTO_APPROVE_REMINDERS", settings.POLICY_AUTO_APPROVE_REMINDERS),
        "POLICY_MAX_PAYOUT_PER_TX": dynamic.get("POLICY_MAX_PAYOUT_PER_TX", settings.POLICY_MAX_PAYOUT_PER_TX),
        "POLICY_MAX_PAYOUT_PER_DAY": dynamic.get("POLICY_MAX_PAYOUT_PER_DAY", settings.POLICY_MAX_PAYOUT_PER_DAY),
        "POLICY_MAX_DISCOUNT_PCT": dynamic.get("POLICY_MAX_DISCOUNT_PCT", settings.POLICY_MAX_DISCOUNT_PCT),
        "POLICY_REQUIRE_APPROVAL_ABOVE": dynamic.get("POLICY_REQUIRE_APPROVAL_ABOVE", settings.POLICY_REQUIRE_APPROVAL_ABOVE),
        "overrides": dynamic,
    }


@router.put("/policy")
def update_policy(payload: PolicyUpdateRequest, tenant_id: str = Depends(get_tenant_id), db: Session = Depends(get_db)):
    """Update runtime policy rule and audit the change."""
    existing = db.query(Policy).filter(Policy.key == payload.key, Policy.tenant_id == tenant_id).order_by(Policy.version.desc()).first()
    new_version = (existing.version + 1) if existing else 1

    entry = Policy(
        tenant_id=tenant_id,
        key=payload.key,
        value=payload.value,
        version=new_version,
    )
    db.add(entry)
    db.commit()

    AuditService.record(
        db=db,
        actor="user",
        event="policy_updated",
        entity_type="policy",
        entity_id=payload.key,
        tenant_id=tenant_id,
        payload={"key": payload.key, "new_value": payload.value, "version": new_version},
    )

    return {"success": True, "key": payload.key, "value": payload.value, "version": new_version}


@router.post("/policy/kill-switch")
def toggle_kill_switch(payload: KillSwitchRequest, tenant_id: str = Depends(get_tenant_id), db: Session = Depends(get_db)):
    """Emergency kill switch: immediately blocks or unblocks all automated write actions."""
    existing = db.query(Policy).filter(Policy.key == "KILL_SWITCH", Policy.tenant_id == tenant_id).order_by(Policy.version.desc()).first()
    new_version = (existing.version + 1) if existing else 1

    entry = Policy(
        tenant_id=tenant_id,
        key="KILL_SWITCH",
        value=payload.active,
        version=new_version,
    )
    db.add(entry)
    db.commit()

    AuditService.record(
        db=db,
        actor="user",
        event="kill_switch_toggled",
        entity_type="policy",
        entity_id="KILL_SWITCH",
        tenant_id=tenant_id,
        payload={"active": payload.active},
    )

    return {"success": True, "kill_switch": payload.active}


@router.get("/audit")
def list_audit_logs(limit: int = 50, offset: int = 0, tenant_id: str = Depends(get_tenant_id), db: Session = Depends(get_db)):
    """Paginated list of cryptographic audit log entries."""
    query = db.query(AuditLog).filter(AuditLog.tenant_id == tenant_id).order_by(AuditLog.seq.desc())
    total = query.count()
    items = query.offset(offset).limit(limit).all()
    return {"total": total, "items": items}


@router.get("/audit/verify")
def verify_audit_ledger(tenant_id: str = Depends(get_tenant_id), db: Session = Depends(get_db)):
    """
    Cryptographic verification endpoint:
    Recalculates every SHA-256 hash in sequence from genesis to verify zero tampering.
    """
    return AuditService.verify_chain(db, tenant_id=tenant_id)

