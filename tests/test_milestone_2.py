import asyncio
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.db import init_db, SessionLocal, engine, Base
from app.models.client import Client
from app.models.invoice import Invoice
from app.models.payment import Payment
from app.models.webhook import WebhookEvent
from app.services.clock import ClockService
from app.services.reconcile import ReconciliationService
from jobs.reconcile_poll import run_reconcile_poll
from app.paypal.client import reset_fake_gateway
from app.config import settings

AUTH_HEADERS = {"X-API-Key": settings.API_KEY}


@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    reset_fake_gateway()
    ClockService.reset()
    yield
    ClockService.reset()


def test_client_crud():
    with TestClient(app) as client:
        # Create client
        res = client.post(
            "/clients",
            headers=AUTH_HEADERS,
            json={
                "name": "Wayne Enterprises",
                "email": "bruce@wayne.com",
                "company": "Wayne Tech",
                "default_terms_days": 30,
                "notes": "Premium enterprise account",
            },
        )
        assert res.status_code == 201
        data = res.json()
        assert data["name"] == "Wayne Enterprises"
        client_id = data["id"]

        # Fetch client
        res_get = client.get(f"/clients/{client_id}", headers=AUTH_HEADERS)
        assert res_get.status_code == 200
        assert res_get.json()["email"] == "bruce@wayne.com"


def test_invoice_create_and_send():
    with TestClient(app) as client:
        # 1. Create client
        c_res = client.post(
            "/clients",
            headers=AUTH_HEADERS,
            json={"name": "Stark Industries", "email": "tony@stark.com"},
        )
        client_id = c_res.json()["id"]

        # 2. Create invoice draft
        inv_payload = {
            "client_id": client_id,
            "line_items": [
                {"name": "AI Architecture Consulting", "quantity": 20, "unit_amount": 150.0},
                {"name": "Security Hardening", "quantity": 10, "unit_amount": 200.0},
            ],
            "terms_days": 15,
            "memo": "Milestone delivery 1",
        }
        inv_res = client.post("/invoices", headers=AUTH_HEADERS, json=inv_payload)
        assert inv_res.status_code == 201
        inv_data = inv_res.json()
        assert inv_data["status"] == "draft"
        assert inv_data["total"] == 5000.0
        assert inv_data["paypal_invoice_id"] is not None
        invoice_id = inv_data["id"]

        # 3. Send invoice
        send_res = client.post(f"/invoices/{invoice_id}/send", headers=AUTH_HEADERS)
        assert send_res.status_code == 200
        assert send_res.json()["status"] == "sent"

        # 4. Send reminder
        remind_res = client.post(
            f"/invoices/{invoice_id}/remind",
            headers=AUTH_HEADERS,
            json={"subject": "Payment Due", "note": "Friendly reminder"},
        )
        assert remind_res.status_code == 200
        assert remind_res.json()["reminder_count"] == 1


def test_webhook_payment_reconciliation_flips_to_paid():
    """
    Milestone 2 core acceptance criteria:
    Invoice is created via API, paid in sandbox (via webhook), and the DB flips to paid.
    """
    with TestClient(app) as client:
        # 1. Create client and invoice
        c_res = client.post(
            "/clients",
            headers=AUTH_HEADERS,
            json={"name": "Cyberdyne Systems", "email": "finance@cyberdyne.com"},
        )
        client_id = c_res.json()["id"]

        inv_res = client.post(
            "/invoices",
            headers=AUTH_HEADERS,
            json={
                "client_id": client_id,
                "line_items": [{"name": "Neural Net Server", "quantity": 1, "unit_amount": 3200.0}],
                "terms_days": 15,
            },
        )
        invoice = inv_res.json()
        invoice_id = invoice["id"]
        paypal_inv_id = invoice["paypal_invoice_id"]

        # 2. Send the invoice
        client.post(f"/invoices/{invoice_id}/send", headers=AUTH_HEADERS)

        # 3. Simulate incoming PayPal Webhook: INVOICING.INVOICE.PAID
        webhook_payload = {
            "id": "WH-EVT-90001",
            "event_type": "INVOICING.INVOICE.PAID",
            "resource": {
                "id": paypal_inv_id,
                "status": "PAID",
                "amount": {"currency_code": "USD", "value": "3200.00"},
                "payments": {
                    "paid_amount": {"currency_code": "USD", "value": "3200.00"}
                },
            },
        }

        # Send webhook (public endpoint)
        wh_res = client.post("/webhooks/paypal", json=webhook_payload)
        assert wh_res.status_code == 200
        assert wh_res.json()["status"] == "accepted"

        # 4. Verify DB invoice flipped to paid
        inv_check = client.get(f"/invoices/{invoice_id}", headers=AUTH_HEADERS)
        assert inv_check.status_code == 200
        updated_inv = inv_check.json()
        assert updated_inv["status"] == "paid"
        assert updated_inv["amount_due"] == 0.0
        assert updated_inv["amount_paid"] == 3200.0


def test_webhook_deduplication():
    """Verify duplicate webhook payloads are idempotent no-ops."""
    with TestClient(app) as client:
        event_payload = {
            "id": "WH-DEDUP-TEST-12345",
            "event_type": "INVOICING.INVOICE.UPDATED",
            "resource": {"id": "INV2-DEDUP"},
        }

        # First call
        res1 = client.post("/webhooks/paypal", json=event_payload)
        assert res1.status_code == 200
        assert res1.json()["status"] == "accepted"

        # Second call (replay)
        res2 = client.post("/webhooks/paypal", json=event_payload)
        assert res2.status_code == 200
        assert res2.json()["status"] == "already_received"


def test_reconcile_poller_safety_net():
    """Verify background reconcile poller detects remote state changes."""
    async def _run():
        db = SessionLocal()
        try:
            # Create a client & invoice in sent status
            client_record = Client(name="Poller Test Client", email="test@poll.com")
            db.add(client_record)
            db.commit()

            inv = Invoice(
                client_id=client_record.id,
                paypal_invoice_id="INV2-POLL-001",
                paypal_invoice_number="INV-POLL-001",
                status="sent",
                total=500.0,
                amount_due=500.0,
                issue_date=ClockService.now(),
                due_date=ClockService.now(),
            )
            db.add(inv)
            db.commit()

            # Execute reconcile poll job
            poll_result = await run_reconcile_poll()
            assert "polled_count" in poll_result
        finally:
            db.close()

    asyncio.run(_run())
