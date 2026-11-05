import pytest
from datetime import timedelta
from fastapi.testclient import TestClient
from app.main import app
from app.db import init_db, SessionLocal, engine, Base
from app.models.client import Client
from app.models.invoice import Invoice
from app.models.dispute import Dispute
from app.services.clock import ClockService
from app.services.risk import RiskScoringService
from app.agent.tone import ToneEngine
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


def test_client_risk_scoring_and_signals():
    """Verify deterministic risk scoring and explainability factors."""
    db = SessionLocal()
    try:
        # Client 1: Chronic late payer
        risky_client = Client(
            name="Late Payer Co",
            late_count=4,
            paid_count=1,
            avg_days_to_pay=28.0,
        )
        db.add(risky_client)
        db.commit()

        # Add an overdue invoice
        inv = Invoice(
            client_id=risky_client.id,
            status="overdue",
            total=3000.0,
            amount_due=3000.0,
            issue_date=ClockService.now() - timedelta(days=30),
            due_date=ClockService.now() - timedelta(days=10),
        )
        db.add(inv)
        db.commit()

        result = RiskScoringService.calculate_client_risk(db, risky_client)
        assert result["score"] >= 60.0
        assert result["label"] == "risky"
        assert any("late payment" in f.lower() for f in result["factors"])
        assert result["signals"]["late_count"] == 4

        # Client 2: Reliable payer
        reliable_client = Client(
            name="Reliable Corp",
            late_count=0,
            paid_count=5,
            avg_days_to_pay=3.0,
        )
        db.add(reliable_client)
        db.commit()

        res_rel = RiskScoringService.calculate_client_risk(db, reliable_client)
        assert res_rel["score"] <= 25.0
        assert res_rel["label"] == "reliable"
    finally:
        db.close()


def test_adaptive_tone_ladder():
    """Verify tone escalation based on overdue days and reminder count."""
    db = SessionLocal()
    try:
        client = Client(name="Acme Partner", late_count=0, paid_count=3)
        db.add(client)
        db.commit()

        now_dt = ClockService.now()

        # 1. Due today / 1 day overdue -> friendly
        inv_friendly = Invoice(
            client_id=client.id,
            paypal_invoice_number="INV-F1",
            status="sent",
            total=1000.0,
            amount_due=1000.0,
            due_date=now_dt - timedelta(days=1),
            reminder_count=0,
        )
        t_friendly = ToneEngine.evaluate_tone_and_signals(db, inv_friendly)
        assert t_friendly["tone"] == "friendly"

        # 2. 5 days overdue -> neutral
        inv_neutral = Invoice(
            client_id=client.id,
            paypal_invoice_number="INV-N1",
            status="overdue",
            total=1000.0,
            amount_due=1000.0,
            due_date=now_dt - timedelta(days=5),
            reminder_count=1,
        )
        t_neutral = ToneEngine.evaluate_tone_and_signals(db, inv_neutral)
        assert t_neutral["tone"] == "neutral"

        # 3. 12 days overdue -> firm
        inv_firm = Invoice(
            client_id=client.id,
            paypal_invoice_number="INV-FIRM1",
            status="overdue",
            total=1000.0,
            amount_due=1000.0,
            due_date=now_dt - timedelta(days=12),
            reminder_count=2,
        )
        t_firm = ToneEngine.evaluate_tone_and_signals(db, inv_firm)
        assert t_firm["tone"] == "firm"

        # 4. 25 days overdue -> final
        inv_final = Invoice(
            client_id=client.id,
            paypal_invoice_number="INV-FIN1",
            status="overdue",
            total=1000.0,
            amount_due=1000.0,
            due_date=now_dt - timedelta(days=25),
            reminder_count=3,
        )
        t_final = ToneEngine.evaluate_tone_and_signals(db, inv_final)
        assert t_final["tone"] == "final"
    finally:
        db.close()


def test_time_travel_and_simulation_suite():
    """Test full time travel and simulation workflow for the hackathon demo."""
    with TestClient(app) as client:
        # 1. Seed demo dataset
        seed_res = client.post("/simulate/seed-demo")
        assert seed_res.status_code == 200
        seed_data = seed_res.json()
        assert seed_data["clients_seeded"] == 6
        assert seed_data["invoices_seeded"] == 15

        # 2. Advance time by 20 days
        tt_res = client.post("/simulate/time-travel", json={"days": 20})
        assert tt_res.status_code == 200
        assert tt_res.json()["offset_days"] == 20.0

        # Check invoices flipped to overdue
        inv_res = client.get("/invoices?status=overdue", headers=AUTH_HEADERS)
        assert inv_res.status_code == 200
        overdue_invoices = inv_res.json()["items"]
        assert len(overdue_invoices) >= 3

        target_inv = overdue_invoices[0]

        # 3. Simulate partial payment
        pay_res = client.post(
            "/simulate/client-pays",
            json={"invoice_id": target_inv["id"], "mode": "partial"},
        )
        assert pay_res.status_code == 200
        assert pay_res.json()["invoice_status"] == "partially_paid"

        # 4. Simulate dispute injection
        disp_res = client.post(
            "/simulate/dispute",
            json={"invoice_id": target_inv["id"], "reason": "UNSATISFACTORY_SERVICES"},
        )
        assert disp_res.status_code == 200
        assert disp_res.json()["status"] == "UNDER_REVIEW"

        # 5. Verify audit log integrity holds across simulated actions
        verify_res = client.get("/audit/verify", headers=AUTH_HEADERS)
        assert verify_res.status_code == 200
        assert verify_res.json()["valid"] is True

