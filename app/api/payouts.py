from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models.payout import Payout
from app.models.contractor import Contractor
from app.services.payout_service import PayoutService

router = APIRouter(prefix="/payouts", tags=["Payouts"])


class PayoutResponse(BaseModel):
    id: str
    contractor_id: str
    invoice_id: Optional[str] = None
    paypal_batch_id: Optional[str] = None
    amount: float
    currency: str
    status: str
    action_id: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)


@router.get("", response_model=List[PayoutResponse])
def list_payouts(
    status_filter: Optional[str] = Query(None, alias="status"),
    db: Session = Depends(get_db),
):
    query = db.query(Payout).filter(Payout.tenant_id == settings.DEFAULT_TENANT_ID)
    if status_filter:
        query = query.filter(Payout.status == status_filter)
    return query.order_by(Payout.created_at.desc()).all()


@router.post("/{id}/approve")
async def approve_payout(id: str, db: Session = Depends(get_db)):
    """Approves and executes a pending contractor payout via PayPal Payouts API."""
    payout = db.query(Payout).filter(Payout.id == id, Payout.tenant_id == settings.DEFAULT_TENANT_ID).first()
    if not payout:
        raise HTTPException(status_code=404, detail="Payout not found")
    if payout.status not in ["proposed", "pending_approval"]:
        raise HTTPException(status_code=400, detail=f"Cannot approve payout in status '{payout.status}'")

    res = await PayoutService.execute_payout_submission(db, payout)
    return res

