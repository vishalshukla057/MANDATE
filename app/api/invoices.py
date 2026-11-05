from datetime import timedelta
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.config import settings
from app.models.invoice import Invoice
from app.models.client import Client
from app.models.payment import Payment
from app.schemas.invoice import (
    InvoiceCreateRequest,
    InvoiceResponse,
    InvoiceListResponse,
    ReminderSendRequest,
    ExternalPaymentRequest,
)
from app.services.clock import ClockService
from app.services.audit import AuditService
from app.services.reconcile import ReconciliationService
from app.paypal.client import get_paypal_gateway, PayPalError

router = APIRouter(prefix="/invoices", tags=["Invoices"])


@router.get("", response_model=InvoiceListResponse)
def list_invoices(
    status_filter: Optional[str] = Query(None, alias="status"),
    client_id: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    query = db.query(Invoice).filter(Invoice.tenant_id == settings.DEFAULT_TENANT_ID)
    if status_filter:
        query = query.filter(Invoice.status == status_filter.lower())
    if client_id:
        query = query.filter(Invoice.client_id == client_id)

    total = query.count()
    items = query.order_by(Invoice.created_at.desc()).offset(offset).limit(limit).all()
    return InvoiceListResponse(items=items, total=total)


@router.post("", response_model=InvoiceResponse, status_code=status.HTTP_201_CREATED)
async def create_invoice(payload: InvoiceCreateRequest, db: Session = Depends(get_db)):
    client = db.query(Client).filter(Client.id == payload.client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    gateway = get_paypal_gateway()
    now_dt = ClockService.now()
    due_dt = payload.due_date or (now_dt + timedelta(days=payload.terms_days))

    # Calculate subtotal and total
    subtotal = sum(item.quantity * item.unit_amount for item in payload.line_items)
    discount_val = 0.0
    if payload.discount_percent and payload.discount_percent > 0:
        discount_val = round(subtotal * (payload.discount_percent / 100.0), 2)
    total = round(subtotal - discount_val, 2)

    # Generate next invoice number via PayPal
    try:
        inv_number = await gateway.generate_next_invoice_number()
    except Exception as exc:
        inv_number = f"INV-{now_dt.strftime('%Y%m%d%H%M%S')}"

    # Build PayPal draft payload
    paypal_items = [
        {
            "name": item.name,
            "description": item.description or item.name,
            "quantity": str(item.quantity),
            "unit_amount": {"currency_code": payload.currency, "value": f"{item.unit_amount:.2f}"},
            "unit_of_measure": item.unit_of_measure or "QUANTITY",
        }
        for item in payload.line_items
    ]

    paypal_draft_body = {
        "detail": {
            "invoice_number": inv_number,
            "invoice_date": now_dt.strftime("%Y-%m-%d"),
            "currency_code": payload.currency,
            "note": payload.memo or "Thank you for your business.",
            "payment_term": {
                "term_type": f"NET_{payload.terms_days}" if payload.terms_days in [10, 15, 30, 45, 60, 90] else "DUE_ON_DATE_SPECIFIED",
                "due_date": due_dt.strftime("%Y-%m-%d"),
            },
        },
        "invoicer": {
            "name": {"given_name": "Collections Copilot Merchant"},
            "email_address": settings.PAYPAL_MERCHANT_EMAIL,
        },
        "primary_recipients": [
            {
                "billing_info": {
                    "name": {"given_name": client.name},
                    "email_address": client.email or "billing@example.com",
                }
            }
        ],
        "items": paypal_items,
    }

    try:
        paypal_res = await gateway.create_draft_invoice(paypal_draft_body)
    except PayPalError as err:
        raise HTTPException(status_code=err.status_code, detail=err.to_dict())

    paypal_inv_id = paypal_res.get("id")
    payer_link = None
    for link in paypal_res.get("links", []):
        if link.get("rel") == "payer-view":
            payer_link = link.get("href")

    line_items_stored = [item.model_dump() for item in payload.line_items]

    invoice = Invoice(
        tenant_id=settings.DEFAULT_TENANT_ID,
        client_id=client.id,
        paypal_invoice_id=paypal_inv_id,
        paypal_invoice_number=inv_number,
        status="draft",
        currency=payload.currency,
        subtotal=subtotal,
        discount_total=discount_val,
        total=total,
        amount_paid=0.0,
        amount_due=total,
        issue_date=now_dt,
        due_date=due_dt,
        terms_days=payload.terms_days,
        line_items=line_items_stored,
        memo=payload.memo,
        payer_view_url=payer_link,
        source_input_id=payload.source_input_id,
    )
    db.add(invoice)
    client.total_billed = round(client.total_billed + total, 2)
    db.commit()
    db.refresh(invoice)

    AuditService.record(
        db=db,
        actor="user",
        event="invoice_draft_created",
        entity_type="invoice",
        entity_id=invoice.id,
        payload={
            "paypal_invoice_id": paypal_inv_id,
            "invoice_number": inv_number,
            "total": total,
            "client_name": client.name,
        },
    )

    return invoice


@router.get("/{id}", response_model=InvoiceResponse)
def get_invoice(id: str, db: Session = Depends(get_db)):
    invoice = db.query(Invoice).filter(Invoice.id == id, Invoice.tenant_id == settings.DEFAULT_TENANT_ID).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return invoice


@router.post("/{id}/send", response_model=InvoiceResponse)
async def send_invoice(id: str, db: Session = Depends(get_db)):
    invoice = db.query(Invoice).filter(Invoice.id == id, Invoice.tenant_id == settings.DEFAULT_TENANT_ID).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if invoice.status != "draft":
        raise HTTPException(status_code=400, detail=f"Cannot send invoice with status '{invoice.status}'")

    gateway = get_paypal_gateway()
    try:
        await gateway.send_invoice(invoice.paypal_invoice_id)
    except PayPalError as err:
        raise HTTPException(status_code=err.status_code, detail=err.to_dict())

    invoice.status = "sent"
    db.commit()
    db.refresh(invoice)

    AuditService.record(
        db=db,
        actor="user",
        event="invoice_sent",
        entity_type="invoice",
        entity_id=invoice.id,
        payload={"paypal_invoice_id": invoice.paypal_invoice_id, "amount_due": invoice.amount_due},
    )
    return invoice


@router.post("/{id}/remind")
async def send_invoice_reminder(id: str, payload: ReminderSendRequest, db: Session = Depends(get_db)):
    invoice = db.query(Invoice).filter(Invoice.id == id, Invoice.tenant_id == settings.DEFAULT_TENANT_ID).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if invoice.status in ["draft", "paid", "cancelled", "refunded"]:
        raise HTTPException(status_code=400, detail=f"Cannot remind for invoice with status '{invoice.status}'")

    gateway = get_paypal_gateway()
    try:
        res = await gateway.send_reminder(
            invoice_id=invoice.paypal_invoice_id,
            subject=payload.subject or "Invoice Reminder",
            note=payload.note or "Outstanding payment reminder.",
        )
    except PayPalError as err:
        raise HTTPException(status_code=err.status_code, detail=err.to_dict())

    invoice.last_reminder_at = ClockService.now()
    invoice.reminder_count += 1
    db.commit()

    AuditService.record(
        db=db,
        actor="user",
        event="reminder_sent",
        entity_type="invoice",
        entity_id=invoice.id,
        payload={"subject": payload.subject, "reminder_count": invoice.reminder_count},
    )
    return {"success": True, "reminder_count": invoice.reminder_count, "result": res}


@router.post("/{id}/cancel", response_model=InvoiceResponse)
async def cancel_invoice(id: str, db: Session = Depends(get_db)):
    invoice = db.query(Invoice).filter(Invoice.id == id, Invoice.tenant_id == settings.DEFAULT_TENANT_ID).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if invoice.status in ["paid", "cancelled"]:
        raise HTTPException(status_code=400, detail=f"Cannot cancel invoice in status '{invoice.status}'")

    gateway = get_paypal_gateway()
    try:
        await gateway.cancel_invoice(
            invoice_id=invoice.paypal_invoice_id,
            subject="Invoice Cancelled",
            note="Invoice has been cancelled by issuer.",
        )
    except PayPalError as err:
        raise HTTPException(status_code=err.status_code, detail=err.to_dict())

    invoice.status = "cancelled"
    invoice.amount_due = 0.0
    db.commit()
    db.refresh(invoice)

    AuditService.record(
        db=db,
        actor="user",
        event="invoice_cancelled",
        entity_type="invoice",
        entity_id=invoice.id,
        payload={"paypal_invoice_id": invoice.paypal_invoice_id},
    )
    return invoice


@router.post("/{id}/payments")
def record_external_payment(id: str, payload: ExternalPaymentRequest, db: Session = Depends(get_db)):
    invoice = db.query(Invoice).filter(Invoice.id == id, Invoice.tenant_id == settings.DEFAULT_TENANT_ID).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    txn_id = f"EXT-{ClockService.now().strftime('%Y%m%d%H%M%S')}"
    inv, payment = ReconciliationService.reconcile_payment(
        db=db,
        paypal_transaction_id=txn_id,
        amount=payload.amount,
        currency=invoice.currency,
        invoice_id=invoice.id,
        method=payload.method,
    )
    return {
        "success": True,
        "payment_id": payment.id if payment else None,
        "invoice_status": inv.status if inv else invoice.status,
        "amount_due": inv.amount_due if inv else invoice.amount_due,
    }

