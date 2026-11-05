import asyncio
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.db import init_db, SessionLocal, engine, Base
from app.models.client import Client
from app.models.invoice import Invoice
from app.models.contractor import Contractor, PayoutRule
from app.models.payout import Payout
from app.models.dispute import Dispute
from app.services.clock import ClockService
from app.services.payout_service import PayoutService
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


def test_contractor_and_payout_rule_creation():
    with TestClient(app) as client:
        # Create contractor
        c_res = client.post(
            "/contractors",
            headers=AUTH_HEADERS,
            json={
                "name": "Sarah Connor",
                "email_or_paypal_id": "sarah@cyberdyne-res.org",
                "default_split_pct": 20.0,
                "notes": "Infrastructure Specialist",
            },
        )
        assert c_res.status_code == 201
        contractor = c_res.json()
        assert contractor["name"] == "Sarah Connor"
        c_id = contractor["id"]

        # Create payout rule
        r_res = client.post(
            "/payout-rules",
            headers=AUTH_HEADERS,
            json={
                "contractor_id": c_id,
                "trigger": "on_invoice_paid",
                "basis": "percent_of_invoice",
                "value": 20.0,
            },
        )
        assert r_res.status_code == 201
        assert r_res.json()["value"] == 20.0


def test_paid_invoice_triggers_payout_lifecycle():
    """
    Milestone 5 core acceptance criteria:
    Paid invoice triggers payout proposed -> approval -> batch payout submitted.
    """
    with TestClient(app) as client:
        # 1. Setup Contractor and 25% Payout Rule
        c_res = client.post(
            "/contractors",
            headers=AUTH_HEADERS,
            json={"name": "Dev Specialist", "email_or_paypal_id": "dev@freelance.org", "default_split_pct": 25.0},
        )
        contractor_id = c_res.json()["id"]

        client.post(
            "/payout-rules",
            headers=AUTH_HEADERS,
            json={"contractor_id": contractor_id, "trigger": "on_invoice_paid", "basis": "percent_of_invoice", "value": 25.0},
        )

        # 2. Setup Client and Invoice
        cl_res = client.post("/clients", headers=AUTH_HEADERS, json={"name": "Omni Consumer Products", "email": "ap@ocp.corp"})
        client_id = cl_res.json()["id"]

        inv_res = client.post(
            "/invoices",
            headers=AUTH_HEADERS,
            json={
                "client_id": client_id,
                "line_items": [{"name": "Cyborg Security", "quantity": 1, "unit_amount": 2000.0}],
                "terms_days": 15,
            },
        )
        invoice = inv_res.json()
        invoice_id = invoice["id"]
        paypal_inv_id = invoice["paypal_invoice_id"]

        # Send the invoice
        client.post(f"/invoices/{invoice_id}/send", headers=AUTH_HEADERS)

        # 3. Simulate payment webhook
        wh_payload = {
            "id": "WH-PAID-PAYOUT-TEST",
            "event_type": "INVOICING.INVOICE.PAID",
            "resource": {
                "id": paypal_inv_id,
                "status": "PAID",
                "amount": {"currency_code": "USD", "value": "2000.00"},
                "payments": {"paid_amount": {"currency_code": "USD", "value": "2000.00"}},
            },
        }
        wh_res = client.post("/webhooks/paypal", json=wh_payload)
        assert wh_res.status_code == 200

        # 4. Check Payout created in pending_approval
        payouts_res = client.get("/payouts", headers=AUTH_HEADERS)
        assert payouts_res.status_code == 200
        payouts_list = payouts_res.json()
        assert len(payouts_list) >= 1

        payout = payouts_list[0]
        assert payout["amount"] == 500.0  # 25% of 2000
        assert payout["status"] == "pending_approval"
        payout_id = payout["id"]

        # 5. Approve payout
        appr_res = client.post(f"/payouts/{payout_id}/approve", headers=AUTH_HEADERS)
        assert appr_res.status_code == 200
        data = appr_res.json()
        assert data["status"] == "submitted"
        assert data["batch_id"].startswith("PB-") or data["batch_id"].startswith("BATCH-")


def test_dispute_copilot_flow():
    """
    Milestone 5 core acceptance criteria:
    Dispute arrives -> evidence drafted -> win likelihood estimated -> response submitted.
    """
    with TestClient(app) as client:
        # Seed demo dataset (has 1 open dispute)
        client.post("/simulate/seed-demo")

        # Fetch disputes
        d_res = client.get("/disputes", headers=AUTH_HEADERS)
        assert d_res.status_code == 200
        disputes = d_res.json()
        assert len(disputes) >= 1

        dispute_id = disputes[0]["id"]

        # Inspect dispute evidence pack & win likelihood
        detail_res = client.get(f"/disputes/{dispute_id}", headers=AUTH_HEADERS)
        assert detail_res.status_code == 200
        detail = detail_res.json()
        assert "evidence_pack" in detail
        assert "win_likelihood" in detail
        assert detail["win_likelihood"]["win_likelihood_pct"] >= 50.0
        assert len(detail["win_likelihood"]["reasons"]) > 0
        assert "refute" in detail["draft_response"].lower() or "deliver" in detail["draft_response"].lower()

        # Submit response to PayPal
        submit_res = client.post(
            f"/disputes/{dispute_id}/submit",
            headers=AUTH_HEADERS,
            json={"message": "All deliverables completed and confirmed by client."},
        )
        assert submit_res.status_code == 200
        assert submit_res.json()["status"] == "RESPONSE_SUBMITTED"

