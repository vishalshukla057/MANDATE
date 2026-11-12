from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.auth_context import get_tenant_id
from app.models.client import Client
from app.models.invoice import Invoice
from app.schemas.client import (
    ClientCreateRequest,
    ClientUpdateRequest,
    ClientResponse,
    ClientRiskProfileResponse,
)

router = APIRouter(prefix="/clients", tags=["Clients"])


@router.get("", response_model=List[ClientResponse])
def list_clients(limit: int = 50, offset: int = 0, tenant_id: str = Depends(get_tenant_id), db: Session = Depends(get_db)):
    return (
        db.query(Client)
        .filter(Client.tenant_id == tenant_id)
        .order_by(Client.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )


@router.post("", response_model=ClientResponse, status_code=status.HTTP_201_CREATED)
def create_client(payload: ClientCreateRequest, tenant_id: str = Depends(get_tenant_id), db: Session = Depends(get_db)):
    client = Client(
        tenant_id=tenant_id,
        name=payload.name,
        email=payload.email,
        company=payload.company,
        default_terms_days=payload.default_terms_days,
        currency=payload.currency,
        notes=payload.notes,
        risk_score=10.0,
        risk_label="reliable",
    )
    db.add(client)
    db.commit()
    db.refresh(client)
    return client


@router.get("/{id}", response_model=ClientResponse)
def get_client(id: str, tenant_id: str = Depends(get_tenant_id), db: Session = Depends(get_db)):
    client = db.query(Client).filter(Client.id == id, Client.tenant_id == tenant_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    return client


@router.patch("/{id}", response_model=ClientResponse)
def update_client(id: str, payload: ClientUpdateRequest, tenant_id: str = Depends(get_tenant_id), db: Session = Depends(get_db)):
    client = db.query(Client).filter(Client.id == id, Client.tenant_id == tenant_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    update_data = payload.model_dump(exclude_unset=True)
    for field, val in update_data.items():
        setattr(client, field, val)

    db.commit()
    db.refresh(client)
    return client


@router.get("/{id}/history")
def get_client_history(id: str, tenant_id: str = Depends(get_tenant_id), db: Session = Depends(get_db)):
    client = db.query(Client).filter(Client.id == id, Client.tenant_id == tenant_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    invoices = db.query(Invoice).filter(Invoice.client_id == id).order_by(Invoice.issue_date.desc()).all()
    return {
        "client_id": client.id,
        "name": client.name,
        "total_invoices": len(invoices),
        "total_billed": client.total_billed,
        "total_paid": client.total_paid,
        "avg_days_to_pay": client.avg_days_to_pay,
        "late_count": client.late_count,
        "paid_count": client.paid_count,
        "invoices": [
            {
                "id": inv.id,
                "paypal_invoice_number": inv.paypal_invoice_number,
                "status": inv.status,
                "total": inv.total,
                "amount_due": inv.amount_due,
                "due_date": inv.due_date,
            }
            for inv in invoices
        ],
    }


@router.get("/{id}/risk", response_model=ClientRiskProfileResponse)
def get_client_risk(id: str, tenant_id: str = Depends(get_tenant_id), db: Session = Depends(get_db)):
    client = db.query(Client).filter(Client.id == id, Client.tenant_id == tenant_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    factors = []
    if client.risk_label == "reliable":
        factors.append("Consistent payment track record with negligible delay")
    elif client.risk_label == "watch":
        factors.append("Occasional payment delays observed in past cycles")
    else:
        factors.append("High late payment frequency or dispute history")

    return ClientRiskProfileResponse(
        client_id=client.id,
        name=client.name,
        risk_score=client.risk_score,
        risk_label=client.risk_label,
        avg_days_to_pay=client.avg_days_to_pay,
        late_count=client.late_count,
        paid_count=client.paid_count,
        factors=factors,
        signals={
            "total_billed": client.total_billed,
            "total_paid": client.total_paid,
            "late_ratio": (client.late_count / max(1, client.paid_count + client.late_count)),
        },
    )

