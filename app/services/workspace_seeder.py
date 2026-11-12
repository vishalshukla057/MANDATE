import uuid
from datetime import timedelta, timezone
from typing import Any, Dict, List

from sqlalchemy.orm import Session

from app.models.agent import AgentAction, AgentRun
from app.models.audit import AuditLog
from app.models.client import Client
from app.models.contractor import Contractor, PayoutRule
from app.models.dispute import Dispute
from app.models.input_record import InputRecord
from app.models.invoice import Invoice
from app.models.offer import Offer
from app.models.payment import Payment
from app.models.payout import Payout
from app.models.policy import Policy
from app.models.reminder import Reminder
from app.services.audit import AuditService
from app.services.clock import ClockService


SAMPLE_CLIENTS = [
    {
        "name": "Apex Innovations",
        "company": "Apex Technologies Inc.",
        "email": "billing@apex-innovations.io",
        "risk_label": "reliable",
        "risk_score": 8.5,
        "avg_days_to_pay": 3.8,
        "late_count": 0,
        "paid_count": 14,
        "default_terms_days": 15,
        "currency": "USD",
    },
    {
        "name": "Vanguard Logistics",
        "company": "Vanguard Global Freight LLC",
        "email": "ap@vanguard-freight.com",
        "risk_label": "risky",
        "risk_score": 84.0,
        "avg_days_to_pay": 28.4,
        "late_count": 6,
        "paid_count": 3,
        "default_terms_days": 30,
        "currency": "USD",
    },
    {
        "name": "Sovereign Capital",
        "company": "Sovereign Capital Partners",
        "email": "ops@sovereigncap.com",
        "risk_label": "watch",
        "risk_score": 38.0,
        "avg_days_to_pay": 13.5,
        "late_count": 2,
        "paid_count": 8,
        "default_terms_days": 15,
        "currency": "EUR",
    },
    {
        "name": "Atlas Heavy Engineering",
        "company": "Atlas Engineering Group AG",
        "email": "invoicing@atlas-eng.com",
        "risk_label": "reliable",
        "risk_score": 14.0,
        "avg_days_to_pay": 6.2,
        "late_count": 1,
        "paid_count": 12,
        "default_terms_days": 30,
        "currency": "GBP",
    },
    {
        "name": "Hyperion Dynamics",
        "company": "Hyperion Dynamics Robotics",
        "email": "accounts@hyperion.tech",
        "risk_label": "risky",
        "risk_score": 72.0,
        "avg_days_to_pay": 24.1,
        "late_count": 5,
        "paid_count": 4,
        "default_terms_days": 15,
        "currency": "USD",
    },
]

SAMPLE_CONTRACTORS = [
    {"name": "Elena Rostova", "email": "elena.dev@contractors.io", "split": 25.0, "notes": "Staff Fullstack Architect"},
    {"name": "Marcus Chen", "email": "marcus.qa@contractors.io", "split": 15.0, "notes": "Security & QA Lead"},
]


def clear_workspace_data(db: Session, tenant_id: str) -> None:
    """Deletes all records belonging to a specific tenant_id in FK-safe order."""
    db.query(AuditLog).filter(AuditLog.tenant_id == tenant_id).delete(synchronize_session=False)
    db.query(AgentAction).filter(AgentAction.tenant_id == tenant_id).delete(synchronize_session=False)
    db.query(AgentRun).filter(AgentRun.tenant_id == tenant_id).delete(synchronize_session=False)
    db.query(InputRecord).filter(InputRecord.tenant_id == tenant_id).delete(synchronize_session=False)
    db.query(Payment).filter(Payment.tenant_id == tenant_id).delete(synchronize_session=False)
    db.query(Reminder).filter(Reminder.tenant_id == tenant_id).delete(synchronize_session=False)
    db.query(Payout).filter(Payout.tenant_id == tenant_id).delete(synchronize_session=False)
    db.query(PayoutRule).filter(PayoutRule.tenant_id == tenant_id).delete(synchronize_session=False)
    db.query(Contractor).filter(Contractor.tenant_id == tenant_id).delete(synchronize_session=False)
    db.query(Dispute).filter(Dispute.tenant_id == tenant_id).delete(synchronize_session=False)
    db.query(Offer).filter(Offer.tenant_id == tenant_id).delete(synchronize_session=False)
    db.query(Invoice).filter(Invoice.tenant_id == tenant_id).delete(synchronize_session=False)
    db.query(Client).filter(Client.tenant_id == tenant_id).delete(synchronize_session=False)
    db.query(Policy).filter(Policy.tenant_id == tenant_id).delete(synchronize_session=False)
    db.commit()


def seed_workspace_data(db: Session, tenant_id: str, reset: bool = False) -> Dict[str, int]:
    """Populates a workspace tenant_id with realistic sample B2B receivables data."""
    if reset:
        clear_workspace_data(db, tenant_id)
    elif db.query(Client).filter(Client.tenant_id == tenant_id).count() > 0:
        return {
            "clients": db.query(Client).filter(Client.tenant_id == tenant_id).count(),
            "invoices": db.query(Invoice).filter(Invoice.tenant_id == tenant_id).count(),
            "already_seeded": 1,
        }

    now_dt = ClockService.now()
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=timezone.utc)

    created_clients: List[Client] = []
    for meta in SAMPLE_CLIENTS:
        c = Client(
            tenant_id=tenant_id,
            name=meta["name"],
            company=meta["company"],
            email=meta["email"],
            default_terms_days=meta["default_terms_days"],
            currency=meta["currency"],
            risk_score=meta["risk_score"],
            risk_label=meta["risk_label"],
            avg_days_to_pay=meta["avg_days_to_pay"],
            late_count=meta["late_count"],
            paid_count=meta["paid_count"],
            total_billed=0.0,
            total_paid=0.0,
        )
        db.add(c)
        created_clients.append(c)
    db.flush()

    contractors: List[Contractor] = []
    for cm in SAMPLE_CONTRACTORS:
        ct = Contractor(
            tenant_id=tenant_id,
            name=cm["name"],
            email_or_paypal_id=cm["email"],
            default_split_pct=cm["split"],
            notes=cm["notes"],
            active=True,
        )
        db.add(ct)
        contractors.append(ct)
    db.flush()

    for ct in contractors:
        db.add(
            PayoutRule(
                tenant_id=tenant_id,
                contractor_id=ct.id,
                trigger="on_invoice_paid",
                basis="percent_of_invoice",
                value=ct.default_split_pct,
                invoice_filter={},
            )
        )

    invoice_specs = [
        (created_clients[0], "Cloud Security & Mandate Audit", 4800.0, "USD", "paid", -18, 15, 0),
        (created_clients[0], "PayPal Orders v2 Integration Retainer", 3200.0, "USD", "sent", -4, 15, 0),
        (created_clients[1], "Kubernetes Cluster Migration", 3400.0, "USD", "overdue", -38, 15, 2),
        (created_clients[1], "Freight Telemetry API Connector", 1950.0, "USD", "disputed", -29, 15, 1),
        (created_clients[2], "Multi-Currency FX Hedging Module", 4100.0, "EUR", "overdue", -21, 15, 1),
        (created_clients[3], "Automated AR Policy Engine Setup", 2750.0, "GBP", "paid", -25, 30, 0),
        (created_clients[4], "Autonomous Agent Guardrail Sprint", 5200.0, "USD", "overdue", -46, 15, 3),
        (created_clients[2], "Q4 Compliance & Ledger Review", 1650.0, "USD", "draft", -1, 15, 0),
    ]

    invoices: List[Invoice] = []
    base_num = 2040
    for idx, (cli, item_name, total, curr, status_val, issue_offset, terms, rem_count) in enumerate(invoice_specs):
        inv_id = str(uuid.uuid4())
        issue_dt = now_dt + timedelta(days=issue_offset)
        due_dt = issue_dt + timedelta(days=terms)
        paid_amt = total if status_val == "paid" else 0.0
        due_amt = 0.0 if status_val == "paid" else total
        inv_num = f"INV-{base_num + idx}"
        pp_inv_id = f"INV2-DEMO-{inv_id[:8].upper()}"

        inv = Invoice(
            id=inv_id,
            tenant_id=tenant_id,
            client_id=cli.id,
            paypal_invoice_id=pp_inv_id,
            paypal_invoice_number=inv_num,
            status=status_val,
            currency=curr,
            subtotal=total,
            discount_total=0.0,
            total=total,
            amount_paid=paid_amt,
            amount_due=due_amt,
            issue_date=issue_dt,
            due_date=due_dt,
            terms_days=terms,
            line_items=[{"name": item_name, "description": item_name, "quantity": 1, "unit_amount": total}],
            memo=f"{item_name} — Net {terms} via PayPal",
            payer_view_url=f"https://www.sandbox.paypal.com/invoice/p/#{pp_inv_id}",
            reminder_count=rem_count,
            last_reminder_at=(now_dt - timedelta(days=3)) if rem_count > 0 else None,
        )
        db.add(inv)
        invoices.append(inv)
        cli.total_billed = round(cli.total_billed + total, 2)
        cli.total_paid = round(cli.total_paid + paid_amt, 2)

        if status_val == "paid":
            db.add(
                Payment(
                    tenant_id=tenant_id,
                    invoice_id=inv.id,
                    paypal_transaction_id=f"TXN-DEMO-{inv.id[:8].upper()}",
                    amount=total,
                    currency=curr,
                    method="PAYPAL",
                    paid_at=due_dt - timedelta(days=2),
                )
            )

        if rem_count > 0:
            db.add(
                Reminder(
                    tenant_id=tenant_id,
                    invoice_id=inv.id,
                    tone="firm" if rem_count >= 2 else "neutral",
                    subject=f"Payment Reminder: Invoice #{inv_num}",
                    body=f"Hello {cli.name}, Invoice #{inv_num} ({curr} {total:.2f}) is awaiting settlement.",
                    reasoning=f"Client risk score {cli.risk_score} with {rem_count} prior reminder(s).",
                    sent_at=now_dt - timedelta(days=3),
                    status="sent",
                )
            )

    db.flush()

    # Seed a pending approval action so Approvals Queue is immediately actionable
    overdue_inv = invoices[2]
    run = AgentRun(
        tenant_id=tenant_id,
        trigger="scheduled_scan",
        input="Review overdue invoice #INV-2042 for Vanguard Logistics and propose early settlement discount.",
        final_message="Proposed 8% early-settlement discount on INV-2042; queued for operator approval.",
        tool_calls_count=2,
        latency_ms=142.0,
    )
    db.add(run)
    db.flush()

    db.add(
        AgentAction(
            tenant_id=tenant_id,
            parent_run_id=run.id,
            kind="propose_discount_offer",
            tool_name="send_reminder",
            args={
                "invoice_id": overdue_inv.id,
                "subject": f"Priority Settlement Notice: {overdue_inv.paypal_invoice_number}",
                "note": "Settle within 48h via PayPal to avoid account escalation.",
                "reasoning": "Client risk score 84.0 (risky), 38 days overdue — escalated for human confirmation.",
            },
            risk_tier="medium",
            status="pending_approval",
            reasoning="High-value overdue account ($3,400) exceeds auto-approve threshold ($250).",
            signals={"days_overdue": 23, "client_risk_score": 84.0, "amount_due": 3400.0},
            policy_decision={"decision": "REQUIRE_APPROVAL", "reasons": ["Amount $3,400.00 exceeds auto-approval cap ($250.00)."]},
        )
    )

    # Seed a pending contractor payout
    db.add(
        Payout(
            tenant_id=tenant_id,
            contractor_id=contractors[0].id,
            invoice_id=invoices[0].id,
            amount=1200.0,
            currency="USD",
            status="pending_approval",
        )
    )

    # Seed an open dispute with carrier tracking ready to verify
    disputed_inv = invoices[3]
    db.add(
        Dispute(
            tenant_id=tenant_id,
            invoice_id=disputed_inv.id,
            paypal_dispute_id=f"PP-D-DEMO-{uuid.uuid4().hex[:6].upper()}",
            transaction_id=f"TXN-DISP-{uuid.uuid4().hex[:6].upper()}",
            reason="MERCHANDISE_OR_SERVICE_NOT_RECEIVED",
            status="UNDER_REVIEW",
            amount=disputed_inv.total,
            due_by=now_dt + timedelta(days=7),
            evidence_pack={
                "tracking_number": "789234891023",
                "carrier": "FedEx Priority Express",
                "uploaded_evidence": [],
            },
            draft_response="Formal merchant evidence dossier prepared for PayPal Dispute Resolution.",
        )
    )

    db.commit()

    AuditService.record(
        db=db,
        actor="system",
        event="workspace_seeded",
        entity_type="workspace",
        entity_id=tenant_id,
        payload={"clients": len(created_clients), "invoices": len(invoices), "contractors": len(contractors)},
        tenant_id=tenant_id,
    )

    return {
        "clients": len(created_clients),
        "invoices": len(invoices),
        "contractors": len(contractors),
        "disputes": 1,
        "pending_approvals": 1,
    }

