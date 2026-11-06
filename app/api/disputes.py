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


class CarrierVerifyRequest(BaseModel):
    tracking_number: str = Field(..., description="Tracking number e.g. 789234891023 (FedEx) or 1Z9999999999999999 (UPS)")


@router.post("/{id}/carrier-verify")
def verify_carrier_and_attach_proof(id: str, payload: CarrierVerifyRequest, db: Session = Depends(get_db)):
    """Verifies shipment with carrier API and automatically attaches cryptographic proof of delivery to dispute."""
    from app.services.carrier_service import CarrierService

    dispute = db.query(Dispute).filter((Dispute.id == id) | (Dispute.paypal_dispute_id == id)).first()
    if not dispute:
        raise HTTPException(status_code=404, detail="Dispute not found")

    carrier_proof = CarrierService.verify_tracking(payload.tracking_number)

    pack = dict(dispute.evidence_pack or {})
    items = pack.get("uploaded_evidence", [])
    # Add or update carrier evidence
    items = [i for i in items if i.get("type") != "carrier_proof_of_delivery"]
    items.append({
        "type": "carrier_proof_of_delivery",
        "carrier": carrier_proof["carrier"],
        "tracking_number": payload.tracking_number,
        "details": carrier_proof,
    })
    pack["uploaded_evidence"] = items
    dispute.evidence_pack = pack

    # Re-calculate win probability
    new_win_prob = CarrierService.calculate_win_probability(pack)

    db.commit()

    AuditService.record(
        db=db,
        actor="carrier_service",
        event="carrier_proof_attached_to_dispute",
        entity_type="dispute",
        entity_id=dispute.id,
        payload={
            "carrier": carrier_proof["carrier"],
            "tracking_number": payload.tracking_number,
            "signature_hash": carrier_proof["signature_proof_hash"],
            "new_win_probability": new_win_prob,
        }
    )

    return {
        "success": True,
        "dispute_id": dispute.id,
        "carrier_proof": carrier_proof,
        "evidence_pack": pack,
        "win_probability": new_win_prob,
    }


@router.get("/{id}/evidence-pack/download")
def download_evidence_pack(id: str, db: Session = Depends(get_db)):
    """Exports structured formal evidence manifest for PayPal dispute submission."""
    from app.services.carrier_service import CarrierService

    dispute = db.query(Dispute).filter((Dispute.id == id) | (Dispute.paypal_dispute_id == id)).first()
    if not dispute:
        raise HTTPException(status_code=404, detail="Dispute not found")

    evidence_pack = DisputeCopilotService.compile_evidence_pack(db, dispute)
    win_likelihood = CarrierService.calculate_win_probability(evidence_pack)

    manifest = {
        "manifest_version": "2024-11-06",
        "paypal_dispute_id": dispute.paypal_dispute_id,
        "disputed_amount": dispute.dispute_amount,
        "dispute_currency": dispute.currency,
        "reason": dispute.reason,
        "win_probability_score": win_likelihood,
        "compiled_evidence": evidence_pack,
        "merchant_response_draft": dispute.draft_response,
        "certification": "I hereby certify under penalty of perjury that the fulfillment proofs and carrier signatures provided are genuine and unaltered.",
    }

    return manifest


