"""Enterprise Collections Dataset Generator & Seeder.

Generates realistic B2B accounts receivable data matching PayPal rails:
- Enterprise clients with varying risk tiers (reliable, watch, risky)
- Diverse invoices across aging buckets (current, 1-30, 31-60, 61-90, 90+ days)
- Multi-currency transactions (USD, EUR, GBP, CAD)
- Carrier-backed dispute cases with tracking numbers & proof of delivery
- Adaptive tone reminder ladders
- Automated contractor splits & payout lifecycle
- Exportable to CSV, JSON, and SQLite
"""

import os
import sys
import json
import csv
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Any

# Ensure parent directory is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings
from app.db import SessionLocal, init_db
from app.models.client import Client
from app.models.invoice import Invoice
from app.models.payment import Payment
from app.models.dispute import Dispute
from app.models.reminder import Reminder
from app.models.contractor import Contractor, PayoutRule
from app.models.payout import Payout
from app.services.audit import AuditService
from app.services.clock import ClockService

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

ENTERPRISE_CLIENTS_META = [
    {"name": "Apex Innovations", "company": "Apex Technologies Inc.", "email": "billing@apex-innovations.io", "risk_label": "reliable", "risk_score": 8.5, "avg_days": 3.8, "late": 0, "paid": 14, "terms": 15},
    {"name": "Vanguard Logistics", "company": "Vanguard Global Freight LLC", "email": "ap@vanguard-freight.com", "risk_label": "risky", "risk_score": 86.0, "avg_days": 28.4, "late": 7, "paid": 3, "terms": 30},
    {"name": "Sovereign Capital", "company": "Sovereign Capital Partners", "email": "ops@sovereigncap.com", "risk_label": "watch", "risk_score": 38.0, "avg_days": 13.5, "late": 2, "paid": 8, "terms": 15},
    {"name": "Atlas Heavy Engineering", "company": "Atlas Engineering Group AG", "email": "invoicing@atlas-eng.com", "risk_label": "reliable", "risk_score": 14.0, "avg_days": 6.2, "late": 1, "paid": 12, "terms": 30},
    {"name": "Hyperion Dynamics", "company": "Hyperion Dynamics Robotics", "email": "accounts@hyperion.tech", "risk_label": "risky", "risk_score": 72.0, "avg_days": 24.1, "late": 5, "paid": 4, "terms": 15},
    {"name": "Beacon Health Systems", "company": "Beacon Healthcare Solutions", "email": "payables@beacon.health", "risk_label": "reliable", "risk_score": 11.0, "avg_days": 4.5, "late": 0, "paid": 9, "terms": 30},
    {"name": "Cyberdyne Systems", "company": "Cyberdyne Autonomous Defense", "email": "finance@cyberdyne.corp", "risk_label": "watch", "risk_score": 44.0, "avg_days": 16.0, "late": 2, "paid": 6, "terms": 30},
    {"name": "Tyrell Corporation", "company": "Tyrell Bio-Engineering Corp", "email": "ap@tyrell-corp.com", "risk_label": "risky", "risk_score": 82.0, "avg_days": 31.0, "late": 6, "paid": 2, "terms": 15},
    {"name": "Umbrella BioLabs", "company": "Umbrella Pharmaceuticals", "email": "billing@umbrella.pharma", "risk_label": "watch", "risk_score": 49.0, "avg_days": 18.2, "late": 3, "paid": 5, "terms": 45},
    {"name": "Weyland-Yutani Corp", "company": "Weyland-Yutani Deep Space Mining", "email": "finance@weyland-yutani.com", "risk_label": "reliable", "risk_score": 19.0, "avg_days": 8.0, "late": 1, "paid": 15, "terms": 30},
    {"name": "Initech Software", "company": "Initech Systems Inc.", "email": "accounting@initech.com", "risk_label": "watch", "risk_score": 35.0, "avg_days": 12.0, "late": 1, "paid": 7, "terms": 15},
    {"name": "Hooli Enterprise", "company": "Hooli Cloud Infrastructure", "email": "vendor-pay@hooli.com", "risk_label": "reliable", "risk_score": 9.0, "avg_days": 4.0, "late": 0, "paid": 18, "terms": 30},
    {"name": "Pied Piper Compression", "company": "Pied Piper Technologies", "email": "treasury@piedpiper.io", "risk_label": "watch", "risk_score": 42.0, "avg_days": 15.4, "late": 2, "paid": 5, "terms": 15},
    {"name": "Massive Dynamic", "company": "Massive Dynamic Labs LLC", "email": "ap@massivedynamic.org", "risk_label": "reliable", "risk_score": 16.0, "avg_days": 7.1, "late": 0, "paid": 11, "terms": 30},
    {"name": "Soylent Logistics", "company": "Soylent Supply Chain Co.", "email": "invoices@soylent.net", "risk_label": "risky", "risk_score": 79.0, "avg_days": 27.5, "late": 5, "paid": 3, "terms": 15},
    {"name": "Globex International", "company": "Globex Overseas Trading", "email": "accounts@globex.intl", "risk_label": "reliable", "risk_score": 21.0, "avg_days": 9.3, "late": 1, "paid": 10, "terms": 30},
]

CONTRACTORS_META = [
    {"name": "Elena Rostova", "email": "elena.dev@contractors.io", "split": 25.0, "notes": "Staff Fullstack Architect & Payments Lead"},
    {"name": "Marcus Chen", "email": "marcus.qa@contractors.io", "split": 15.0, "notes": "Senior Security & Automated QA Specialist"},
    {"name": "Amina Al-Mansoor", "email": "amina.ml@contractors.io", "split": 20.0, "notes": "Lead Risk Modeling & Machine Learning Engineer"},
    {"name": "Devon Vance", "email": "devon.ops@contractors.io", "split": 10.0, "notes": "Cloud Infrastructure & SRE Consultant"},
]

DISPUTE_SCENARIOS = [
    {
        "client_name": "Vanguard Logistics",
        "reason": "MERCHANDISE_OR_SERVICE_NOT_RECEIVED",
        "amount": 3400.0,
        "carrier": "FedEx Express",
        "tracking_number": "771298492014",
        "signed_by": "J. KOVACS (Freight Receiving Dock 4)",
        "win_probability": 0.94,
    },
    {
        "client_name": "Tyrell Corporation",
        "reason": "UNAUTHORIZED_TRANSACTION",
        "amount": 5800.0,
        "carrier": "UPS Worldwide Saver",
        "tracking_number": "1Z9999999999999999",
        "signed_by": "R. DECKARD (Security Gate 2)",
        "win_probability": 0.88,
    },
    {
        "client_name": "Hyperion Dynamics",
        "reason": "SIGNIFICANTLY_NOT_AS_DESCRIBED",
        "amount": 2100.0,
        "carrier": "DHL Express",
        "tracking_number": "DHL-9842104821",
        "signed_by": "ELECTRONIC ACCEPTANCE PROTOCOL 0x4A",
        "win_probability": 0.82,
    },
]

SERVICES_CATALOG = [
    ("Enterprise Cloud Architecture Review", 185.0, 40),
    ("PayPal Orders v2 Integration & Hardening", 210.0, 25),
    ("Autonomous Agent Policy Guardrail Setup", 195.0, 30),
    ("Zero-Day Fraud Detection Pipeline", 225.0, 20),
    ("Multi-Currency FX Hedging Implementation", 200.0, 35),
    ("Automated Accounts Receivable Engine", 175.0, 50),
    ("Dispute Copilot Evidence Package Compiler", 190.0, 15),
    ("Smart Webhook Deduplication System", 180.0, 24),
    ("PCI-DSS Level 1 Compliance Audit", 250.0, 16),
    ("Real-Time Merkle Tree Tamper-Evident Ledger", 230.0, 32),
]

CURRENCIES = ["USD", "USD", "USD", "EUR", "GBP", "CAD"]


def generate_dataset(db_commit: bool = True) -> Dict[str, Any]:
    """Generates the full dataset, optionally commits to SQLite, and exports CSV/JSON."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db = SessionLocal()
    init_db()

    now_dt = ClockService.now()
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=timezone.utc)

    # 1. Seed Contractors & Rules
    contractors: List[Contractor] = []
    contractor_rows = []
    for c_meta in CONTRACTORS_META:
        existing = db.query(Contractor).filter(Contractor.email_or_paypal_id == c_meta["email"]).first()
        if not existing:
            c = Contractor(
                name=c_meta["name"],
                email_or_paypal_id=c_meta["email"],
                default_split_pct=c_meta["split"],
                notes=c_meta["notes"],
                active=True,
            )
            db.add(c)
            db.commit()
            db.refresh(c)
            rule = PayoutRule(
                contractor_id=c.id,
                trigger="on_invoice_paid",
                basis="percent_of_invoice",
                value=c_meta["split"],
            )
            db.add(rule)
            db.commit()
            contractors.append(c)
        else:
            contractors.append(existing)

        contractor_rows.append({
            "id": contractors[-1].id,
            "name": c_meta["name"],
            "paypal_recipient": c_meta["email"],
            "default_split_percentage": c_meta["split"],
            "role": c_meta["notes"],
        })

    # 2. Seed Clients
    client_objects = {}
    clients_rows = []
    for meta in ENTERPRISE_CLIENTS_META:
        c = db.query(Client).filter(Client.name == meta["name"]).first()
        if not c:
            c = Client(
                name=meta["name"],
                email=meta["email"],
                company=meta["company"],
                risk_score=meta["risk_score"],
                risk_label=meta["risk_label"],
                avg_days_to_pay=meta["avg_days"],
                late_count=meta["late"],
                paid_count=meta["paid"],
                total_billed=0.0,
                total_paid=0.0,
            )
            db.add(c)
            db.commit()
            db.refresh(c)
        client_objects[meta["name"]] = c
        clients_rows.append({
            "client_id": c.id,
            "name": c.name,
            "company": c.company,
            "email": c.email,
            "risk_tier": c.risk_label,
            "risk_score": c.risk_score,
            "avg_days_to_pay": c.avg_days_to_pay,
            "late_payments_count": c.late_count,
            "successful_payments_count": c.paid_count,
            "credit_terms_days": meta["terms"],
        })

    # 3. Seed Invoices Across All Aging Buckets
    statuses = ["paid", "paid", "paid", "sent", "overdue", "partially_paid", "disputed", "draft"]
    invoices_rows = []
    payments_rows = []
    disputes_rows = []
    payouts_rows = []

    inv_counter = 1000

    for client_name, client in client_objects.items():
        # Generate 4-7 invoices per client over past 120 days
        num_invoices = random.randint(4, 7)
        client_total_billed = 0.0
        client_total_paid = 0.0

        for i in range(num_invoices):
            inv_counter += 1
            days_ago = random.randint(3, 110)
            issue_date = now_dt - timedelta(days=days_ago)
            terms = 15 if client.risk_label == "risky" else 30
            due_date = issue_date + timedelta(days=terms)

            # Determine realistic status based on age & risk
            if days_ago > terms + 15:
                if client.risk_label == "risky":
                    status = random.choice(["overdue", "disputed", "partially_paid", "paid"])
                else:
                    status = "paid"
            elif days_ago > terms:
                status = "overdue" if client.risk_label != "reliable" else random.choice(["paid", "overdue"])
            else:
                status = random.choice(["sent", "sent", "paid", "draft"])

            # Pick 1-3 line items
            num_items = random.randint(1, 3)
            items = []
            inv_subtotal = 0.0
            for _ in range(num_items):
                desc, rate, hrs = random.choice(SERVICES_CATALOG)
                item_total = rate * hrs
                inv_subtotal += item_total
                items.append({
                    "description": desc,
                    "quantity": hrs,
                    "unit_price": rate,
                    "total": item_total,
                })

            currency = random.choice(CURRENCIES)
            inv_total = round(inv_subtotal, 2)
            amount_paid = 0.0
            amount_due = inv_total

            if status == "paid":
                amount_paid = inv_total
                amount_due = 0.0
            elif status == "partially_paid":
                amount_paid = round(inv_total * 0.4, 2)
                amount_due = round(inv_total - amount_paid, 2)
            elif status == "disputed":
                amount_paid = inv_total
                amount_due = 0.0

            client_total_billed += inv_total
            client_total_paid += amount_paid

            inv_num = f"INV-{inv_counter}"
            paypal_inv_id = f"PP-INV-{inv_counter}"

            # Check if invoice exists
            existing_inv = db.query(Invoice).filter(Invoice.paypal_invoice_number == inv_num).first()
            if not existing_inv:
                inv = Invoice(
                    client_id=client.id,
                    paypal_invoice_id=paypal_inv_id,
                    paypal_invoice_number=inv_num,
                    status=status,
                    currency=currency,
                    subtotal=inv_total,
                    discount_total=0.0,
                    total=inv_total,
                    amount_paid=amount_paid,
                    amount_due=amount_due,
                    issue_date=issue_date,
                    due_date=due_date,
                    terms_days=terms,
                    line_items=items,
                    memo=f"PayPal Settlement Rail - Automated Collections Invoice #{inv_num}",
                    reminder_count=random.randint(1, 3) if status == "overdue" else 0,
                    last_reminder_at=now_dt - timedelta(days=2) if status == "overdue" else None,
                )
                db.add(inv)
                db.commit()
                db.refresh(inv)
            else:
                inv = existing_inv

            invoices_rows.append({
                "invoice_id": inv.id,
                "invoice_number": inv_num,
                "client_name": client.name,
                "currency": currency,
                "subtotal": inv.subtotal,
                "total": inv.total,
                "amount_paid": inv.amount_paid,
                "amount_due": inv.amount_due,
                "status": inv.status,
                "issue_date": inv.issue_date.strftime("%Y-%m-%d"),
                "due_date": inv.due_date.strftime("%Y-%m-%d"),
                "aging_bucket": (
                    "PAID" if status == "paid" else
                    "CURRENT" if now_dt <= due_date else
                    "1-30 DAYS" if (now_dt - due_date).days <= 30 else
                    "31-60 DAYS" if (now_dt - due_date).days <= 60 else
                    "61-90 DAYS" if (now_dt - due_date).days <= 90 else "90+ DAYS"
                ),
            })

            # Record Payment if paid
            if amount_paid > 0:
                payment_id = f"CAP-{inv_counter}-{random.randint(1000, 9999)}"
                existing_pmt = db.query(Payment).filter(Payment.paypal_transaction_id == payment_id).first()
                if not existing_pmt:
                    pmt = Payment(
                        invoice_id=inv.id,
                        paypal_transaction_id=payment_id,
                        amount=amount_paid,
                        currency=currency,
                        method="PAYPAL_ORDERS_V2_CAPTURE",
                        paid_at=due_date - timedelta(days=random.randint(1, 5)),
                    )
                    db.add(pmt)
                    db.commit()
                payments_rows.append({
                    "transaction_id": payment_id,
                    "invoice_number": inv_num,
                    "client_name": client.name,
                    "amount": amount_paid,
                    "currency": currency,
                    "rail": "PayPal Orders v2 Settlement",
                    "payment_date": inv.due_date.strftime("%Y-%m-%d"),
                })

                # Contractor Payout split for paid invoices
                if status == "paid" and contractors:
                    c_lead = contractors[0]
                    payout_amt = round(amount_paid * (c_lead.default_split_pct / 100.0), 2)
                    p_batch = f"BATCH-PO-{inv_counter}"
                    existing_payout = db.query(Payout).filter(Payout.paypal_batch_id == p_batch).first()
                    if not existing_payout:
                        po = Payout(
                            contractor_id=c_lead.id,
                            invoice_id=inv.id,
                            paypal_batch_id=p_batch,
                            amount=payout_amt,
                            currency=currency,
                            status="succeeded",
                        )
                        db.add(po)
                        db.commit()
                    payouts_rows.append({
                        "payout_batch_id": p_batch,
                        "contractor_name": c_lead.name,
                        "source_invoice": inv_num,
                        "split_percentage": c_lead.default_split_pct,
                        "payout_amount": payout_amt,
                        "currency": currency,
                        "status": "succeeded",
                    })

        # Update client aggregate metrics
        client.total_billed = round(client_total_billed, 2)
        client.total_paid = round(client_total_paid, 2)
        db.commit()

    # 4. Seed Carrier Proof Disputes
    for d_idx, d_scenario in enumerate(DISPUTE_SCENARIOS, start=1):
        c_obj = client_objects.get(d_scenario["client_name"])
        inv = db.query(Invoice).filter(Invoice.client_id == c_obj.id).first() if c_obj else None
        d_id = f"PP-D-990{d_idx}"
        existing_d = db.query(Dispute).filter(Dispute.paypal_dispute_id == d_id).first()
        if not existing_d:
            evidence_pack = {
                "carrier": d_scenario["carrier"],
                "tracking_number": d_scenario["tracking_number"],
                "signed_by": d_scenario["signed_by"],
                "delivery_timestamp": (now_dt - timedelta(days=20)).isoformat(),
                "gps_coordinates": "37.7749° N, 122.4194° W",
                "estimated_win_probability": d_scenario["win_probability"],
                "merchant_statement": f"Delivery completed by {d_scenario['carrier']} with signature confirmed on file.",
            }
            dispute = Dispute(
                tenant_id=settings.DEFAULT_TENANT_ID,
                paypal_dispute_id=d_id,
                invoice_id=inv.id if inv else None,
                reason=d_scenario["reason"],
                status="UNDER_REVIEW",
                amount=d_scenario["amount"],
                due_by=now_dt + timedelta(days=7),
                evidence_pack=evidence_pack,
                draft_response=f"Formal evidence submitted: Verified carrier proof of delivery {d_scenario['tracking_number']}.",
            )
            db.add(dispute)
            db.commit()
        disputes_rows.append({
            "dispute_id": d_id,
            "client_name": d_scenario["client_name"],
            "disputed_amount": d_scenario["amount"],
            "reason": d_scenario["reason"],
            "carrier": d_scenario["carrier"],
            "tracking_number": d_scenario["tracking_number"],
            "signed_by": d_scenario["signed_by"],
            "win_probability_pct": int(d_scenario["win_probability"] * 100),
            "status": "UNDER_REVIEW",
        })

    # 5. Export Datasets to CSV and JSON
    _export_csv(DATA_DIR / "clients.csv", clients_rows)
    _export_csv(DATA_DIR / "invoices.csv", invoices_rows)
    _export_csv(DATA_DIR / "payments.csv", payments_rows)
    _export_csv(DATA_DIR / "disputes.csv", disputes_rows)
    _export_csv(DATA_DIR / "contractor_payouts.csv", payouts_rows)

    full_dataset = {
        "metadata": {
            "title": "PayPal Collections Copilot Enterprise Dataset",
            "version": "2.4.0",
            "generated_at": now_dt.isoformat(),
            "total_clients": len(clients_rows),
            "total_invoices": len(invoices_rows),
            "total_payments": len(payments_rows),
            "total_disputes": len(disputes_rows),
            "total_payouts": len(payouts_rows),
        },
        "clients": clients_rows,
        "invoices": invoices_rows,
        "payments": payments_rows,
        "disputes": disputes_rows,
        "contractor_payouts": payouts_rows,
    }

    json_path = DATA_DIR / "enterprise_collections_dataset.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(full_dataset, f, indent=2)

    # 6. Audit entry
    AuditService.record(
        db=db,
        actor="dataset_generator",
        event="enterprise_dataset_generated",
        entity_type="system",
        payload={
            "clients_count": len(clients_rows),
            "invoices_count": len(invoices_rows),
            "payments_count": len(payments_rows),
            "disputes_count": len(disputes_rows),
        }
    )

    db.close()
    return full_dataset


def _export_csv(file_path: Path, rows: List[Dict[str, Any]]):
    if not rows:
        return
    with open(file_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    result = generate_dataset()
    meta = result["metadata"]
    print("=" * 60)
    print(" [✓] ENTERPRISE DATASET GENERATED SUCCESSFULLY")
    print(f"  • Clients Seeded:     {meta['total_clients']}")
    print(f"  • Invoices Seeded:    {meta['total_invoices']}")
    print(f"  • Payments Tracked:   {meta['total_payments']}")
    print(f"  • Disputes Packaged:  {meta['total_disputes']}")
    print(f"  • Payouts Executed:   {meta['total_payouts']}")
    print(f"  • Output Directory:   {DATA_DIR}")
    print("=" * 60)
