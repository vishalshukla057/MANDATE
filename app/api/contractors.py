from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models.contractor import Contractor, PayoutRule

router = APIRouter(tags=["Contractors & Payout Rules"])


class ContractorCreateRequest(BaseModel):
    name: str = Field(..., min_length=1)
    email_or_paypal_id: str
    default_split_pct: float = 0.0
    notes: Optional[str] = None


class ContractorResponse(BaseModel):
    id: str
    name: str
    email_or_paypal_id: str
    default_split_pct: float
    notes: Optional[str] = None
    active: bool
    model_config = ConfigDict(from_attributes=True)


class PayoutRuleCreateRequest(BaseModel):
    contractor_id: str
    trigger: str = "on_invoice_paid"
    basis: str = "percent_of_invoice"
    value: float
    invoice_filter: Optional[dict] = None


class PayoutRuleResponse(BaseModel):
    id: str
    contractor_id: str
    trigger: str
    basis: str
    value: float
    invoice_filter: dict
    model_config = ConfigDict(from_attributes=True)


@router.get("/contractors", response_model=List[ContractorResponse])
def list_contractors(db: Session = Depends(get_db)):
    return db.query(Contractor).filter(Contractor.tenant_id == settings.DEFAULT_TENANT_ID).all()


@router.post("/contractors", response_model=ContractorResponse, status_code=status.HTTP_201_CREATED)
def create_contractor(payload: ContractorCreateRequest, db: Session = Depends(get_db)):
    c = Contractor(
        tenant_id=settings.DEFAULT_TENANT_ID,
        name=payload.name,
        email_or_paypal_id=payload.email_or_paypal_id,
        default_split_pct=payload.default_split_pct,
        notes=payload.notes,
        active=True,
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


@router.get("/payout-rules", response_model=List[PayoutRuleResponse])
def list_payout_rules(db: Session = Depends(get_db)):
    return db.query(PayoutRule).filter(PayoutRule.tenant_id == settings.DEFAULT_TENANT_ID).all()


@router.post("/payout-rules", response_model=PayoutRuleResponse, status_code=status.HTTP_201_CREATED)
def create_payout_rule(payload: PayoutRuleCreateRequest, db: Session = Depends(get_db)):
    contractor = db.query(Contractor).filter(Contractor.id == payload.contractor_id).first()
    if not contractor:
        raise HTTPException(status_code=404, detail="Contractor not found")

    rule = PayoutRule(
        tenant_id=settings.DEFAULT_TENANT_ID,
        contractor_id=payload.contractor_id,
        trigger=payload.trigger,
        basis=payload.basis,
        value=payload.value,
        invoice_filter=payload.invoice_filter or {},
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule
