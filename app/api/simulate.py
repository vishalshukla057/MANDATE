from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db, SessionLocal, Base, engine
from app.models.client import Client
from app.models.invoice import Invoice
from app.models.payment import Payment
from app.models.contractor import Contractor, PayoutRule
from app.models.payout import Payout
from app.models.dispute import Dispute
from app.models.agent import AgentAction, AgentRun
from app.models.audit import AuditLog
from app.models.reminder import Reminder
from app.models.offer import Offer
from app.services.clock import ClockService
from app.services.audit import AuditService
from app.services.reconcile import ReconciliationService
from app.services.risk import RiskScoringService
from jobs.overdue_scan import run_overdue_scan
from app.paypal.client import reset_fake_gateway

router = APIRouter(prefix="/simulate", tags=["Sandbox Simulation"])


class TimeTravelRequest(BaseModel):
    days: float = Field(..., description="Number of days to advance logical clock")


class ClientPaysRequest(BaseModel):
    invoice_id: str
    mode: str = Field("full", pattern="^(full|partial|late)$")
    amount: Optional[float] = None


class DisputeSimRequest(BaseModel):
    invoice_id: str
    reason: Optional[str] = "MERCHANDISE_OR_SERVICE_NOT_RECEIVED"


@router.post("/time-travel")
async def time_travel(payload: TimeTravelRequest, db: Session = Depends(get_db)):
    """Advances application logical clock by N days and triggers overdue scanning."""
    if settings.APP_ENV == "prod":
        raise HTTPException(status_code=403, detail="Simulation endpoints are disabled in production.")

    new_time = ClockService.time_travel(payload.days)
    scan_res = await run_overdue_scan()

    AuditService.record(
        db=db,
        actor="system",
        event="simulation_time_travel",
        entity_type="clock",
        payload={"advanced_days": payload.days, "new_time": new_time.isoformat(), "scan_result": scan_res},
    )

    return {
        "success": True,
        "offset_days": ClockService.get_offset_days(),
        "current_simulated_time": new_time.isoformat(),
        "scan_result": scan_res,
    }


@router.post("/client-pays")
def simulate_client_payment(payload: ClientPaysRequest, db: Session = Depends(get_db)):
    """Simulates client paying an invoice (full, partial, or late)."""
    if settings.APP_ENV == "prod":
        raise HTTPException(status_code=403, detail="Simulation endpoints are disabled in production.")

    inv = db.query(Invoice).filter((Invoice.id == payload.invoice_id) | (Invoice.paypal_invoice_id == payload.invoice_id)).first()
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")

    pay_amount = payload.amount
    if not pay_amount:
        if payload.mode == "partial":
            pay_amount = round(inv.amount_due * 0.4, 2)
        else:
            pay_amount = inv.amount_due

    txn_id = f"SIM-TXN-{ClockService.now().strftime('%Y%m%d%H%M%S')}"
    updated_inv, payment = ReconciliationService.reconcile_payment(
        db=db,
        paypal_transaction_id=txn_id,
        amount=pay_amount,
        currency=inv.currency,
        invoice_id=inv.id,
        method="SIMULATED_PAYPAL",
    )

    return {
        "success": True,
        "mode": payload.mode,
        "amount_paid": pay_amount,
        "invoice_status": updated_inv.status if updated_inv else inv.status,
        "amount_due": updated_inv.amount_due if updated_inv else inv.amount_due,
        "payment_id": payment.id if payment else None,
    }


@router.post("/dispute")
def simulate_dispute(payload: DisputeSimRequest, db: Session = Depends(get_db)):
    """Injects a simulated customer dispute into the database and audit trail."""
    if settings.APP_ENV == "prod":
        raise HTTPException(status_code=403, detail="Simulation endpoints are disabled in production.")

    inv = db.query(Invoice).filter((Invoice.id == payload.invoice_id) | (Invoice.paypal_invoice_id == payload.invoice_id)).first()
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")

    dispute_id = f"PP-D-{ClockService.now().strftime('%Y%m%d%H%M%S')}"
    due_date = ClockService.now() + timedelta(days=10)

    evidence_pack = {
        "invoice": {
            "id": inv.id,
            "number": inv.paypal_invoice_number,
            "total": inv.total,
            "line_items": inv.line_items,
            "issued_date": inv.issue_date.isoformat(),
        },
        "payments": [
            {"amount": p.amount, "paid_at": p.paid_at.isoformat(), "txn_id": p.paypal_transaction_id}
            for p in db.query(Payment).filter(Payment.invoice_id == inv.id).all()
        ],
        "reminders": [
            {"tone": r.tone, "sent_at": r.sent_at.isoformat() if r.sent_at else None, "subject": r.subject}
            for r in db.query(Reminder).filter(Reminder.invoice_id == inv.id).all()
        ],
    }

    dispute = Dispute(
        tenant_id=settings.DEFAULT_TENANT_ID,
        paypal_dispute_id=dispute_id,
        invoice_id=inv.id,
        reason=payload.reason or "MERCHANDISE_OR_SERVICE_NOT_RECEIVED",
        status="UNDER_REVIEW",
        amount=inv.total,
        due_by=due_date,
        evidence_pack=evidence_pack,
        draft_response=(
            f"Seller delivered all contracted milestones according to specification on Invoice #{inv.paypal_invoice_number}. "
            f"Services were fully rendered and accepted prior to billing."
        ),
    )
    db.add(dispute)
    inv.status = "disputed"
    db.commit()
    db.refresh(dispute)

    AuditService.record(
        db=db,
        actor="paypal",
        event="dispute_simulated",
        entity_type="dispute",
        entity_id=dispute.id,
        payload={"paypal_dispute_id": dispute_id, "amount": inv.total, "reason": payload.reason},
    )

    return {"success": True, "dispute_id": dispute.id, "paypal_dispute_id": dispute_id, "status": dispute.status}


@router.post("/seed-demo")
def seed_demo_dataset(db: Session = Depends(get_db)):
    """
    Creates complete hackathon demo dataset:
    - 6 clients with differing payment histories (1 reliable, 1 chronic late payer used in demo)
    - 15 invoices across all statuses (draft, sent, paid, overdue, disputed)
    - 2 contractors with payout rules
    - 1 open dispute
    """
    if settings.APP_ENV == "prod":
        raise HTTPException(status_code=403, detail="Simulation endpoints are disabled in production.")

    # 1. Clear existing data
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    reset_fake_gateway()
    ClockService.reset()

    # 2. Clients
    now_dt = ClockService.now()
    clients_data = [
        # Reliable client
        Client(
            name="Apex Innovations",
            email="accounting@apex.io",
            company="Apex Technologies Inc.",
            risk_score=10.0,
            risk_label="reliable",
            avg_days_to_pay=4.2,
            late_count=0,
            paid_count=8,
            total_billed=18500.0,
            total_paid=18500.0,
        ),
        # Chronic late payer (used in demo story)
        Client(
            name="Vanguard Logistics",
            email="billing@vanguard-freight.com",
            company="Vanguard Logistics LLC",
            risk_score=85.0,
            risk_label="risky",
            avg_days_to_pay=26.5,
            late_count=4,
            paid_count=1,
            total_billed=12000.0,
            total_paid=3000.0,
            notes="Chronic late payer; requires aggressive tone escalation.",
        ),
        # Watch client
        Client(
            name="Sovereign Capital",
            email="ops@sovereigncap.com",
            company="Sovereign Capital Partners",
            risk_score=40.0,
            risk_label="watch",
            avg_days_to_pay=14.0,
            late_count=1,
            paid_count=3,
            total_billed=9500.0,
            total_paid=7500.0,
        ),
        # New client
        Client(
            name="Beacon Health",
            email="pay@beacon.health",
            company="Beacon Health Systems",
            risk_score=15.0,
            risk_label="reliable",
            avg_days_to_pay=0.0,
            late_count=0,
            paid_count=0,
            total_billed=0.0,
            total_paid=0.0,
        ),
        Client(
            name="Atlas Heavy Engineering",
            email="invoicing@atlas-eng.com",
            company="Atlas Engineering Group",
            risk_score=22.0,
            risk_label="reliable",
            avg_days_to_pay=7.5,
            late_count=0,
            paid_count=5,
            total_billed=14200.0,
            total_paid=14200.0,
        ),
        Client(
            name="Hyperion Dynamics",
            email="ap@hyperion.tech",
            company="Hyperion Dynamics Corp",
            risk_score=68.0,
            risk_label="risky",
            avg_days_to_pay=21.0,
            late_count=3,
            paid_count=2,
            total_billed=11000.0,
            total_paid=5500.0,
        ),
    ]
    for c in clients_data:
        db.add(c)
    db.commit()

    apex = clients_data[0]
    vanguard = clients_data[1]
    sovereign = clients_data[2]
    atlas = clients_data[4]
    hyperion = clients_data[5]

    # 3. 15 Invoices across statuses
    invoices_data = [
        # Apex (paid & on-time)
        Invoice(client_id=apex.id, paypal_invoice_id="INV-APX-001", paypal_invoice_number="INV-1001", status="paid", total=2500.0, amount_paid=2500.0, amount_due=0.0, issue_date=now_dt - timedelta(days=40), due_date=now_dt - timedelta(days=25), line_items=[{"description": "Sprint 1", "quantity": 1, "unit_price": 2500.0}]),
        Invoice(client_id=apex.id, paypal_invoice_id="INV-APX-002", paypal_invoice_number="INV-1002", status="paid", total=3000.0, amount_paid=3000.0, amount_due=0.0, issue_date=now_dt - timedelta(days=20), due_date=now_dt - timedelta(days=5), line_items=[{"description": "Sprint 2", "quantity": 1, "unit_price": 3000.0}]),
        Invoice(client_id=apex.id, paypal_invoice_id="INV-APX-003", paypal_invoice_number="INV-1003", status="sent", total=4200.0, amount_paid=0.0, amount_due=4200.0, issue_date=now_dt - timedelta(days=2), due_date=now_dt + timedelta(days=13), line_items=[{"description": "Quarterly Retainer", "quantity": 1, "unit_price": 4200.0}]),

        # Vanguard (Chronic late - overdue, disputed, draft)
        Invoice(client_id=vanguard.id, paypal_invoice_id="INV-VAN-001", paypal_invoice_number="INV-2001", status="overdue", total=3400.0, amount_paid=0.0, amount_due=3400.0, issue_date=now_dt - timedelta(days=35), due_date=now_dt - timedelta(days=14), reminder_count=2, last_reminder_at=now_dt - timedelta(days=4), line_items=[{"description": "Route Optimization Engine", "quantity": 40, "unit_price": 85.0}]),
        Invoice(client_id=vanguard.id, paypal_invoice_id="INV-VAN-002", paypal_invoice_number="INV-2002", status="disputed", total=1800.0, amount_paid=1800.0, amount_due=0.0, issue_date=now_dt - timedelta(days=50), due_date=now_dt - timedelta(days=30), line_items=[{"description": "Dispatcher Dashboard", "quantity": 1, "unit_price": 1800.0}]),
        Invoice(client_id=vanguard.id, paypal_invoice_id="INV-VAN-003", paypal_invoice_number="INV-2003", status="draft", total=2100.0, amount_paid=0.0, amount_due=2100.0, issue_date=now_dt, due_date=now_dt + timedelta(days=15), line_items=[{"description": "API Integration Consulting", "quantity": 14, "unit_price": 150.0}]),

        # Sovereign Capital (partially paid & sent)
        Invoice(client_id=sovereign.id, paypal_invoice_id="INV-SOV-001", paypal_invoice_number="INV-3001", status="partially_paid", total=5000.0, amount_paid=2000.0, amount_due=3000.0, issue_date=now_dt - timedelta(days=18), due_date=now_dt - timedelta(days=3), line_items=[{"description": "Risk Analytics Module", "quantity": 1, "unit_price": 5000.0}]),
        Invoice(client_id=sovereign.id, paypal_invoice_id="INV-SOV-002", paypal_invoice_number="INV-3002", status="sent", total=2500.0, amount_paid=0.0, amount_due=2500.0, issue_date=now_dt - timedelta(days=5), due_date=now_dt + timedelta(days=10), line_items=[{"description": "Security Review", "quantity": 1, "unit_price": 2500.0}]),

        # Atlas Engineering
        Invoice(client_id=atlas.id, paypal_invoice_id="INV-ATL-001", paypal_invoice_number="INV-4001", status="paid", total=6000.0, amount_paid=6000.0, amount_due=0.0, issue_date=now_dt - timedelta(days=60), due_date=now_dt - timedelta(days=45), line_items=[{"description": "SCADA Firmware Architecture", "quantity": 1, "unit_price": 6000.0}]),
        Invoice(client_id=atlas.id, paypal_invoice_id="INV-ATL-002", paypal_invoice_number="INV-4002", status="overdue", total=4500.0, amount_paid=0.0, amount_due=4500.0, issue_date=now_dt - timedelta(days=22), due_date=now_dt - timedelta(days=5), line_items=[{"description": "IoT Telemetry Pipeline", "quantity": 1, "unit_price": 4500.0}]),

        # Hyperion Dynamics
        Invoice(client_id=hyperion.id, paypal_invoice_id="INV-HYP-001", paypal_invoice_number="INV-5001", status="paid", total=3500.0, amount_paid=3500.0, amount_due=0.0, issue_date=now_dt - timedelta(days=45), due_date=now_dt - timedelta(days=30), line_items=[{"description": "Data Pipeline", "quantity": 1, "unit_price": 3500.0}]),
        Invoice(client_id=hyperion.id, paypal_invoice_id="INV-HYP-002", paypal_invoice_number="INV-5002", status="overdue", total=2000.0, amount_paid=0.0, amount_due=2000.0, issue_date=now_dt - timedelta(days=30), due_date=now_dt - timedelta(days=10), line_items=[{"description": "Pipeline Maintenance", "quantity": 1, "unit_price": 2000.0}]),
        Invoice(client_id=hyperion.id, paypal_invoice_id="INV-HYP-003", paypal_invoice_number="INV-5003", status="sent", total=1500.0, amount_paid=0.0, amount_due=1500.0, issue_date=now_dt - timedelta(days=3), due_date=now_dt + timedelta(days=12), line_items=[{"description": "Monthly Support", "quantity": 1, "unit_price": 1500.0}]),
        Invoice(client_id=hyperion.id, paypal_invoice_id="INV-HYP-004", paypal_invoice_number="INV-5004", status="cancelled", total=800.0, amount_paid=0.0, amount_due=0.0, issue_date=now_dt - timedelta(days=25), due_date=now_dt - timedelta(days=10), line_items=[{"description": "Adhoc Investigation", "quantity": 1, "unit_price": 800.0}]),
        Invoice(client_id=hyperion.id, paypal_invoice_id="INV-HYP-005", paypal_invoice_number="INV-5005", status="refunded", total=1200.0, amount_paid=0.0, amount_due=0.0, issue_date=now_dt - timedelta(days=40), due_date=now_dt - timedelta(days=25), line_items=[{"description": "Deprecate Tooling", "quantity": 1, "unit_price": 1200.0}]),
    ]

    for inv in invoices_data:
        db.add(inv)
    db.commit()

    # 4. Contractors & Payout Rules
    c1 = Contractor(name="Elena Rostova", email_or_paypal_id="elena.dev@contractors.io", default_split_pct=25.0, notes="Lead Fullstack Engineer")
    c2 = Contractor(name="Marcus Chen", email_or_paypal_id="marcus.qa@contractors.io", default_split_pct=15.0, notes="Senior QA & Security Specialist")
    db.add(c1)
    db.add(c2)
    db.commit()

    r1 = PayoutRule(contractor_id=c1.id, trigger="on_invoice_paid", basis="percent_of_invoice", value=25.0)
    r2 = PayoutRule(contractor_id=c2.id, trigger="on_invoice_paid", basis="percent_of_invoice", value=15.0)
    db.add(r1)
    db.add(r2)
    db.commit()

    # 5. Open Dispute on Vanguard INV-2002
    disp_inv = db.query(Invoice).filter(Invoice.paypal_invoice_number == "INV-2002").first()
    dispute = Dispute(
        tenant_id=settings.DEFAULT_TENANT_ID,
        paypal_dispute_id="PP-D-88204",
        invoice_id=disp_inv.id if disp_inv else None,
        reason="MERCHANDISE_OR_SERVICE_NOT_RECEIVED",
        status="UNDER_REVIEW",
        amount=1800.0,
        due_by=now_dt + timedelta(days=5),
        evidence_pack={"milestones_delivered": ["Dashboard v1", "API keys delivered"], "sign_off_date": (now_dt - timedelta(days=45)).isoformat()},
        draft_response="Seller delivered complete source repository and credentials to client engineering lead on sign-off.",
    )
    db.add(dispute)
    db.commit()

    # Initial audit genesis entry
    AuditService.record(
        db=db,
        actor="system",
        event="demo_database_seeded",
        entity_type="system",
        payload={"clients_count": len(clients_data), "invoices_count": len(invoices_data), "contractors_count": 2},
    )

    return {
        "success": True,
        "clients_seeded": len(clients_data),
        "invoices_seeded": len(invoices_data),
        "contractors_seeded": 2,
        "disputes_seeded": 1,
    }


@router.post("/reset")
def reset_database(db: Session = Depends(get_db)):
    """Wipes all demo data and resets clock."""
    if settings.APP_ENV == "prod":
        raise HTTPException(status_code=403, detail="Simulation endpoints are disabled in production.")

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    reset_fake_gateway()
    ClockService.reset()

    AuditService.record(
        db=db,
        actor="system",
        event="system_reset",
        entity_type="system",
        payload={"status": "clean"},
    )

    return {"success": True, "message": "Demo data wiped and system reset."}
