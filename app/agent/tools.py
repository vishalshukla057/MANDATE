import hashlib
import json
from typing import Any, Callable, Dict, List, Optional
from sqlalchemy.orm import Session

from app.models.client import Client
from app.models.invoice import Invoice
from app.models.payment import Payment
from app.models.reminder import Reminder
from app.models.offer import Offer
from app.models.contractor import Contractor
from app.models.payout import Payout
from app.models.dispute import Dispute
from app.models.agent import AgentAction
from app.services.clock import ClockService
from app.services.audit import AuditService
from app.services.reconcile import ReconciliationService
from app.paypal.client import get_paypal_gateway, PayPalError
from app.agent.parsing import parse_invoice_input_string


class ToolDefinition:
    def __init__(
        self,
        name: str,
        description: str,
        parameters: Dict[str, Any],
        risk_tier: str,
        handler: Callable,
    ):
        self.name = name
        self.description = description
        self.parameters = parameters
        self.risk_tier = risk_tier
        self.handler = handler

    def to_anthropic_tool(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.parameters,
        }


# Handlers
async def handle_parse_invoice_input(db: Session, args: Dict[str, Any], **kwargs) -> Dict[str, Any]:
    text = args.get("text", "")
    parsed = await parse_invoice_input_string(text, db)
    return parsed.model_dump()


async def handle_lookup_client(db: Session, args: Dict[str, Any], **kwargs) -> Dict[str, Any]:
    client_id = args.get("client_id")
    name = args.get("name")
    query = db.query(Client)
    if client_id:
        c = query.filter(Client.id == client_id).first()
    elif name:
        c = query.filter(Client.name.ilike(f"%{name}%")).first()
    else:
        return {"found": False, "error": "Must specify client_id or name"}

    if not c:
        return {"found": False, "message": "Client not found"}

    return {
        "found": True,
        "id": c.id,
        "name": c.name,
        "email": c.email,
        "company": c.company,
        "risk_score": c.risk_score,
        "risk_label": c.risk_label,
        "total_billed": c.total_billed,
        "total_paid": c.total_paid,
        "paid_count": c.paid_count,
        "late_count": c.late_count,
        "avg_days_to_pay": c.avg_days_to_pay,
        "notes": c.notes,
    }


async def handle_list_invoices(db: Session, args: Dict[str, Any], **kwargs) -> Dict[str, Any]:
    status_filter = args.get("status")
    client_id = args.get("client_id")
    query = db.query(Invoice)
    if status_filter:
        query = query.filter(Invoice.status == status_filter.lower())
    if client_id:
        query = query.filter(Invoice.client_id == client_id)

    invoices = query.order_by(Invoice.issue_date.desc()).limit(20).all()
    return {
        "count": len(invoices),
        "invoices": [
            {
                "id": inv.id,
                "paypal_invoice_id": inv.paypal_invoice_id,
                "paypal_invoice_number": inv.paypal_invoice_number,
                "status": inv.status,
                "total": inv.total,
                "amount_due": inv.amount_due,
                "due_date": inv.due_date.isoformat(),
            }
            for inv in invoices
        ],
    }


async def handle_get_invoice(db: Session, args: Dict[str, Any], **kwargs) -> Dict[str, Any]:
    inv_id = args.get("invoice_id")
    inv = db.query(Invoice).filter((Invoice.id == inv_id) | (Invoice.paypal_invoice_id == inv_id)).first()
    if not inv:
        return {"found": False, "error": f"Invoice {inv_id} not found"}

    payments = db.query(Payment).filter(Payment.invoice_id == inv.id).all()
    reminders = db.query(Reminder).filter(Reminder.invoice_id == inv.id).all()

    return {
        "found": True,
        "id": inv.id,
        "paypal_invoice_id": inv.paypal_invoice_id,
        "paypal_invoice_number": inv.paypal_invoice_number,
        "status": inv.status,
        "currency": inv.currency,
        "total": inv.total,
        "amount_paid": inv.amount_paid,
        "amount_due": inv.amount_due,
        "due_date": inv.due_date.isoformat(),
        "line_items": inv.line_items,
        "payer_view_url": inv.payer_view_url,
        "payments": [{"amount": p.amount, "paid_at": p.paid_at.isoformat(), "method": p.method} for p in payments],
        "reminders_count": len(reminders),
    }


async def handle_create_invoice_draft(db: Session, args: Dict[str, Any], **kwargs) -> Dict[str, Any]:
    client_name = args.get("client_name")
    client_email = args.get("client_email")
    client_id = args.get("client_id")
    line_items = args.get("line_items", [])
    terms_days = args.get("terms_days", 15)
    currency = args.get("currency", "USD")

    client = None
    if client_id:
        client = db.query(Client).filter(Client.id == client_id).first()
    elif client_name:
        client = db.query(Client).filter(Client.name.ilike(f"%{client_name}%")).first()

    if not client:
        # Create new client automatically if not found
        client = Client(name=client_name or "New Client", email=client_email, currency=currency, default_terms_days=terms_days)
        db.add(client)
        db.commit()
        db.refresh(client)

    now_dt = ClockService.now()
    from datetime import timedelta
    due_dt = now_dt + timedelta(days=terms_days)

    subtotal = sum(float(item.get("quantity", 1)) * float(item.get("unit_price", 0)) for item in line_items)
    gateway = get_paypal_gateway()
    inv_num = await gateway.generate_next_invoice_number()

    paypal_draft_body = {
        "detail": {
            "invoice_number": inv_num,
            "invoice_date": now_dt.strftime("%Y-%m-%d"),
            "currency_code": currency,
            "payment_term": {"term_type": f"NET_{terms_days}", "due_date": due_dt.strftime("%Y-%m-%d")},
        },
        "invoicer": {"name": {"given_name": "Collections Copilot"}, "email_address": "merchant@copilot.io"},
        "primary_recipients": [{"billing_info": {"name": {"given_name": client.name}, "email_address": client.email or "client@example.com"}}],
        "items": [
            {
                "name": it.get("description", "Item"),
                "quantity": str(it.get("quantity", 1)),
                "unit_amount": {"currency_code": currency, "value": f"{float(it.get('unit_price', 0)):.2f}"},
            }
            for it in line_items
        ],
    }

    paypal_res = await gateway.create_draft_invoice(paypal_draft_body)
    payer_link = None
    for link in paypal_res.get("links", []):
        if link.get("rel") == "payer-view":
            payer_link = link.get("href")

    invoice = Invoice(
        client_id=client.id,
        paypal_invoice_id=paypal_res.get("id"),
        paypal_invoice_number=inv_num,
        status="draft",
        currency=currency,
        subtotal=subtotal,
        total=subtotal,
        amount_paid=0.0,
        amount_due=subtotal,
        issue_date=now_dt,
        due_date=due_dt,
        terms_days=terms_days,
        line_items=line_items,
        payer_view_url=payer_link,
    )
    db.add(invoice)
    client.total_billed = round(client.total_billed + subtotal, 2)
    db.commit()
    db.refresh(invoice)

    return {
        "success": True,
        "invoice_id": invoice.id,
        "paypal_invoice_id": invoice.paypal_invoice_id,
        "paypal_invoice_number": invoice.paypal_invoice_number,
        "total": invoice.total,
        "payer_view_url": invoice.payer_view_url,
    }


async def handle_send_invoice(db: Session, args: Dict[str, Any], **kwargs) -> Dict[str, Any]:
    invoice_id = args.get("invoice_id")
    inv = db.query(Invoice).filter((Invoice.id == invoice_id) | (Invoice.paypal_invoice_id == invoice_id)).first()
    if not inv:
        return {"success": False, "error": f"Invoice {invoice_id} not found"}

    gateway = get_paypal_gateway()
    await gateway.send_invoice(inv.paypal_invoice_id)
    inv.status = "sent"
    db.commit()
    return {"success": True, "invoice_id": inv.id, "status": "sent", "payer_view_url": inv.payer_view_url}


async def handle_send_reminder(db: Session, args: Dict[str, Any], **kwargs) -> Dict[str, Any]:
    invoice_id = args.get("invoice_id")
    subject = args.get("subject", "Payment Reminder")
    body = args.get("body", "Kindly check the invoice.")
    tone = args.get("tone", "friendly")
    reasoning = args.get("reasoning", "Follow-up schedule")

    inv = db.query(Invoice).filter((Invoice.id == invoice_id) | (Invoice.paypal_invoice_id == invoice_id)).first()
    if not inv:
        return {"success": False, "error": f"Invoice {invoice_id} not found"}

    gateway = get_paypal_gateway()
    await gateway.send_reminder(inv.paypal_invoice_id, subject=subject, note=body)

    inv.last_reminder_at = ClockService.now()
    inv.reminder_count += 1

    rem = Reminder(
        invoice_id=inv.id,
        sent_at=ClockService.now(),
        tone=tone,
        subject=subject,
        body=body,
        reasoning=reasoning,
        status="sent",
    )
    db.add(rem)
    db.commit()
    return {"success": True, "reminder_count": inv.reminder_count, "tone": tone}


TOOL_REGISTRY: Dict[str, ToolDefinition] = {
    "parse_invoice_input": ToolDefinition(
        name="parse_invoice_input",
        description="Extract messy text, email, or transcript into a structured invoice draft.",
        parameters={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Raw unformatted text, email, or voice note transcript"},
                "reasoning": {"type": "string", "description": "Why this input is being parsed"},
            },
            "required": ["text", "reasoning"],
        },
        risk_tier="read",
        handler=handle_parse_invoice_input,
    ),
    "lookup_client": ToolDefinition(
        name="lookup_client",
        description="Fetch client profile, payment history, notes, and risk score.",
        parameters={
            "type": "object",
            "properties": {
                "client_id": {"type": "string", "description": "UUID of client"},
                "name": {"type": "string", "description": "Name or company of client"},
                "reasoning": {"type": "string", "description": "Why client details are needed"},
            },
            "required": ["reasoning"],
        },
        risk_tier="read",
        handler=handle_lookup_client,
    ),
    "list_invoices": ToolDefinition(
        name="list_invoices",
        description="Filter and list invoices by status, client, or date.",
        parameters={
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "Filter by status: draft, sent, paid, overdue, etc."},
                "client_id": {"type": "string", "description": "Filter by client ID"},
                "reasoning": {"type": "string", "description": "Why invoices are listed"},
            },
            "required": ["reasoning"],
        },
        risk_tier="read",
        handler=handle_list_invoices,
    ),
    "get_invoice": ToolDefinition(
        name="get_invoice",
        description="Retrieve full details for a specific invoice, including line items and payment timeline.",
        parameters={
            "type": "object",
            "properties": {
                "invoice_id": {"type": "string", "description": "Internal UUID or PayPal invoice ID"},
                "reasoning": {"type": "string", "description": "Why invoice details are needed"},
            },
            "required": ["invoice_id", "reasoning"],
        },
        risk_tier="read",
        handler=handle_get_invoice,
    ),
    "create_invoice_draft": ToolDefinition(
        name="create_invoice_draft",
        description="Create a new PayPal draft invoice from structured line items.",
        parameters={
            "type": "object",
            "properties": {
                "client_name": {"type": "string", "description": "Client or company name"},
                "client_email": {"type": "string", "description": "Client email"},
                "client_id": {"type": "string", "description": "Existing client UUID (optional)"},
                "line_items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "description": {"type": "string"},
                            "quantity": {"type": "number"},
                            "unit_price": {"type": "number"},
                        },
                        "required": ["description", "quantity", "unit_price"],
                    },
                },
                "terms_days": {"type": "integer", "description": "Payment terms (e.g. 15, 30)"},
                "currency": {"type": "string", "default": "USD"},
                "reasoning": {"type": "string", "description": "Why this invoice draft is created"},
            },
            "required": ["client_name", "line_items", "reasoning"],
        },
        risk_tier="low",
        handler=handle_create_invoice_draft,
    ),
    "send_invoice": ToolDefinition(
        name="send_invoice",
        description="Send an existing PayPal draft invoice to the client.",
        parameters={
            "type": "object",
            "properties": {
                "invoice_id": {"type": "string", "description": "Invoice UUID or PayPal invoice ID"},
                "reasoning": {"type": "string", "description": "Justification for sending"},
            },
            "required": ["invoice_id", "reasoning"],
        },
        risk_tier="medium",
        handler=handle_send_invoice,
    ),
    "send_reminder": ToolDefinition(
        name="send_reminder",
        description="Send a payment reminder for an open/overdue invoice through the PayPal remind endpoint.",
        parameters={
            "type": "object",
            "properties": {
                "invoice_id": {"type": "string"},
                "tone": {"type": "string", "enum": ["friendly", "neutral", "firm", "final"]},
                "subject": {"type": "string"},
                "body": {"type": "string"},
                "reasoning": {"type": "string", "description": "Signals and rationale justifying the tone"},
            },
            "required": ["invoice_id", "tone", "subject", "body", "reasoning"],
        },
        risk_tier="low",
        handler=handle_send_reminder,
    ),
}

