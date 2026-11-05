from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models.dispute import Dispute
from app.services.audit import AuditService
from app.services.feed import feed_broker
from app.agent.dispute_writer import DisputeCopilotService
from app.paypal.client import get_paypal_gateway, PayPalError

router = APIRouter(prefix="/disputes", tags=["Disputes"])


class DisputeEvidenceRequest(BaseModel):
    evidence_type: str = Field(..., description="e.g. proof_of_delivery, client_chat, work_log")
    details: Dict[str, Any]


class DisputeSubmitRequest(BaseModel):
    message: Optional[str] = None


@router.get("")
def list_disputes(db: Session = Depends(get_db)):
    disputes = db.query(Dispute).filter(Dispute.tenant_id == settings.DEFAULT_TENANT_ID).order_by(Dispute.created_at.desc()).all()
    return disputes


@router.get("/{id}")
def get_dispute(id: str, db: Session = Depends(get_db)):
    dispute = db.query(Dispute).filter((Dispute.id == id) | (Dispute.paypal_dispute_id == id)).first()
    if not dispute:
        raise HTTPException(status_code=404, detail="Dispute not found")

    evidence_pack = DisputeCopilotService.compile_evidence_pack(db, dispute)
    win_likelihood = DisputeCopilotService.estimate_win_likelihood(evidence_pack)

    return {
        "dispute": dispute,
        "evidence_pack": evidence_pack,
        "win_likelihood": win_likelihood,
        "draft_response": dispute.draft_response,
    }


@router.post("/{id}/evidence")
def add_dispute_evidence(id: str, payload: DisputeEvidenceRequest, db: Session = Depends(get_db)):
    dispute = db.query(Dispute).filter((Dispute.id == id) | (Dispute.paypal_dispute_id == id)).first()
    if not dispute:
        raise HTTPException(status_code=404, detail="Dispute not found")

    pack = dict(dispute.evidence_pack or {})
    items = pack.get("uploaded_evidence", [])
    items.append({"type": payload.evidence_type, "details": payload.details})
    pack["uploaded_evidence"] = items
    dispute.evidence_pack = pack
    db.commit()

    return {"success": True, "evidence_count": len(items)}


@router.post("/{id}/regenerate-draft")
def regenerate_dispute_draft(id: str, db: Session = Depends(get_db)):
    dispute = db.query(Dispute).filter((Dispute.id == id) | (Dispute.paypal_dispute_id == id)).first()
    if not dispute:
        raise HTTPException(status_code=404, detail="Dispute not found")

    evidence_pack = DisputeCopilotService.compile_evidence_pack(db, dispute)
    win_likelihood = DisputeCopilotService.estimate_win_likelihood(evidence_pack)
    draft = DisputeCopilotService.draft_dispute_response(evidence_pack)

    dispute.draft_response = draft
    db.commit()

    return {
        "success": True,
        "draft_response": draft,
        "win_likelihood": win_likelihood,
    }


@router.post("/{id}/submit")
async def submit_dispute_response(id: str, payload: DisputeSubmitRequest, db: Session = Depends(get_db)):
    """Submits evidence and response message to PayPal Disputes API (requires operator confirmation)."""
    dispute = db.query(Dispute).filter((Dispute.id == id) | (Dispute.paypal_dispute_id == id)).first()
    if not dispute:
        raise HTTPException(status_code=404, detail="Dispute not found")

    msg = payload.message or dispute.draft_response
    gateway = get_paypal_gateway()

    try:
        await gateway.send_dispute_message(dispute.paypal_dispute_id, message=msg)
        dispute.status = "RESPONSE_SUBMITTED"
        db.commit()

        AuditService.record(
            db=db,
            actor="user",
            event="dispute_response_submitted",
            entity_type="dispute",
            entity_id=dispute.id,
            payload={"paypal_dispute_id": dispute.paypal_dispute_id, "message_preview": msg[:80]},
        )

        feed_broker.publish(
            "dispute_submitted",
            {"dispute_id": dispute.id, "paypal_dispute_id": dispute.paypal_dispute_id, "status": "RESPONSE_SUBMITTED"},
        )
        return {"success": True, "dispute_id": dispute.id, "status": "RESPONSE_SUBMITTED"}
    except PayPalError as pe:
        raise HTTPException(status_code=pe.status_code, detail=pe.to_dict())
