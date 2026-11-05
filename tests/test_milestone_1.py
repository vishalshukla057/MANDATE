import asyncio
import pytest
from datetime import datetime, timezone
from fastapi.testclient import TestClient
from app.main import app
from app.db import init_db, SessionLocal, engine, Base
from app.models.client import Client
from app.models.invoice import Invoice
from app.services.clock import ClockService
from app.services.audit import AuditService, GENESIS_HASH
from app.paypal.client import FakePayPalGateway, reset_fake_gateway
from app.config import settings


@pytest.fixture(autouse=True)
def setup_database():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    reset_fake_gateway()
    ClockService.reset()
    yield
    ClockService.reset()


def test_clock_service():
    """Test clock service logical time and time travel."""
    start = ClockService.now()
    assert start.tzinfo is not None

    # Time travel 10.5 days forward
    advanced = ClockService.time_travel(10.5)
    diff = (advanced - start).total_seconds()
    assert 10.49 * 86400 <= diff <= 10.51 * 86400
    assert abs(ClockService.get_offset_days() - 10.5) < 0.01

    # Reset
    reset_time = ClockService.reset()
    assert abs(ClockService.get_offset_days()) < 0.001


def test_audit_service_hash_chain():
    """Test cryptographic SHA-256 hash chaining and tamper verification."""
    db = SessionLocal()
    try:
        # Clear existing audit logs for test isolation
        from app.models.audit import AuditLog
        db.query(AuditLog).delete()
        db.commit()

        # Record 3 events
        entry1 = AuditService.record(
            db=db,
            actor="system",
            event="system_boot",
            entity_type="server",
            payload={"version": "1.0.0"},
            entity_id="srv-1",
        )
        assert entry1.prev_hash == GENESIS_HASH
        assert len(entry1.hash) == 64

        entry2 = AuditService.record(
            db=db,
            actor="agent",
            event="action_proposed",
            entity_type="invoice",
            payload={"amount": 500.0, "currency": "USD"},
            entity_id="inv-101",
        )
        assert entry2.prev_hash == entry1.hash

        entry3 = AuditService.record(
            db=db,
            actor="user",
            event="approval_granted",
            entity_type="invoice",
            payload={"approved": True},
            entity_id="inv-101",
        )
        assert entry3.prev_hash == entry2.hash

        # Verify chain integrity
        result = AuditService.verify_chain(db)
        assert result["valid"] is True
        assert result["count"] == 3

        # Simulate tampering
        entry2.payload = {"amount": 999999.0, "currency": "USD"}  # Tampered!
        db.commit()

        tampered_result = AuditService.verify_chain(db)
        assert tampered_result["valid"] is False
        assert tampered_result["broken_at_seq"] == 2
        assert "Tampered hash" in tampered_result["reason"]
    finally:
        db.close()


def test_fake_paypal_gateway():
    """Test deterministic fake PayPal gateway."""
    async def _run():
        gateway = FakePayPalGateway()

        # Invoicing flow
        inv_num = await gateway.generate_next_invoice_number()
        assert inv_num.startswith("INV-")

        draft = await gateway.create_draft_invoice({
            "detail": {"currency_code": "USD", "invoice_number": inv_num},
            "items": [{"name": "Consulting", "quantity": 10, "unit_amount": {"value": "100.00"}}],
        })
        assert draft["status"] == "DRAFT"
        assert draft["amount"]["value"] == "1000.00"
        assert "_paypal_debug_id" in draft

        sent = await gateway.send_invoice(draft["id"])
        assert sent["_paypal_debug_id"] is not None

        fetched = await gateway.get_invoice(draft["id"])
        assert fetched["status"] == "SENT"

        # Orders / Checkout
        order = await gateway.create_order({
            "intent": "CAPTURE",
            "purchase_units": [{"amount": {"currency_code": "USD", "value": "250.00"}}],
        })
        assert order["status"] == "CREATED"
        captured = await gateway.capture_order(order["id"])
        assert captured["status"] == "COMPLETED"

        # Payouts
        payout = await gateway.create_payout_batch({
            "items": [{"amount": {"value": "75.00", "currency": "USD"}, "receiver": "contractor@example.com"}]
        })
        assert payout["batch_header"]["batch_status"] == "SUCCESS"

    asyncio.run(_run())


def test_fastapi_health_and_correlation_id():
    """Test FastAPI application boots and health endpoint returns correlation ID."""
    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert "clock_time" in data
        assert "X-Correlation-ID" in response.headers
        assert response.headers["X-Correlation-ID"].startswith("cid-")


def test_database_persistence():
    """Verify SQLAlchemy models and persistence."""
    db = SessionLocal()
    try:
        client = Client(
            name="Acme Corp",
            email="finance@acme.com",
            company="Acme Corporation",
            risk_score=15.0,
            risk_label="reliable",
        )
        db.add(client)
        db.commit()
        db.refresh(client)

        invoice = Invoice(
            client_id=client.id,
            status="draft",
            currency="USD",
            subtotal=1200.0,
            total=1200.0,
            amount_due=1200.0,
            issue_date=ClockService.now(),
            due_date=ClockService.now(),
            terms_days=15,
            line_items=[{"description": "Engineering Services", "quantity": 15, "unit_price": 80.0}],
        )
        db.add(invoice)
        db.commit()
        db.refresh(invoice)

        fetched_inv = db.query(Invoice).filter(Invoice.id == invoice.id).first()
        assert fetched_inv is not None
        assert fetched_inv.client_id == client.id
        assert fetched_inv.subtotal == 1200.0
        assert fetched_inv.line_items[0]["unit_price"] == 80.0
    finally:
        db.close()
