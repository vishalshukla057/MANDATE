import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.db import init_db, SessionLocal, engine, Base
from app.models.client import Client
from app.models.invoice import Invoice
from app.models.agent import AgentAction, AgentRun
from app.services.clock import ClockService
from app.services.audit import AuditService
from app.paypal.client import reset_fake_gateway
from app.agent.parsing import parse_messy_input_heuristically
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


def test_structured_parsing_heuristics():
    """Verify parsing engine adheres to strict spec extraction rules."""
    # Test 1: Standard hours and rate
    p1 = parse_messy_input_heuristically("Invoice Acme 40 hours at $85, net 15")
    assert p1.client.name == "Acme"
    assert p1.line_items[0].quantity == 40.0
    assert p1.line_items[0].unit_price == 85.0
    assert p1.terms_days == 15
    assert "client_email" in p1.missing_fields  # Never invent email!

    # Test 2: Half upfront
    p2 = parse_messy_input_heuristically("Bill Stark Ind 20 hours at $200 with half upfront, net 30")
    assert p2.terms_days == 30
    assert p2.line_items[0].quantity == 10.0  # 50% of 20
    assert any("half upfront" in a.lower() for a in p2.assumptions)

    # Test 3: Email detection
    p3 = parse_messy_input_heuristically("Charge Wayne Tech $1200 for cloud setup. Send to bruce@wayne.com")
    assert p3.client.email == "bruce@wayne.com"
    assert "client_email" not in p3.missing_fields


def test_agent_orchestrator_milestone_3_acceptance():
    """
    Milestone 3 core done criteria:
    'Invoice Acme 40 hours at $85, net 15' produces a correct draft,
    then a send action that appears in the approval queue (above $250 threshold) with reasoning.
    """
    with TestClient(app) as client:
        # User submits messy prompt
        prompt = "Invoice Acme 40 hours at $85, net 15"
        res = client.post("/agent/message", headers=AUTH_HEADERS, json={"message": prompt})
        assert res.status_code == 200
        data = res.json()
        assert "run_id" in data
        assert "Acme" in data["final_message"]
        assert "3,400" in data["final_message"] or "3400" in data["final_message"]

        # Check approval queue for pending send_invoice action
        appr_res = client.get("/approvals?status=pending_approval", headers=AUTH_HEADERS)
        assert appr_res.status_code == 200
        pending_list = appr_res.json()
        assert len(pending_list) >= 1

        send_action = next((a for a in pending_list if a["tool_name"] == "send_invoice"), None)
        assert send_action is not None
        assert send_action["status"] == "pending_approval"
        assert send_action["risk_tier"] == "medium"
        assert len(send_action["reasoning"]) > 0
        assert any("exceeds" in r for r in send_action["policy_decision"]["reasons"])


def test_approval_lifecycle_execution():
    """Verify approving a queued action executes it against PayPal and flips status."""
    with TestClient(app) as client:
        # Create an action via agent
        prompt = "Invoice Globex Corp 10 hours at $100, net 15"
        client.post("/agent/message", headers=AUTH_HEADERS, json={"message": prompt})

        # Fetch the pending approval
        appr_res = client.get("/approvals?status=pending_approval", headers=AUTH_HEADERS)
        pending = appr_res.json()
        action_id = pending[0]["id"]

        # Approve the action
        approve_res = client.post(f"/approvals/{action_id}/approve", headers=AUTH_HEADERS)
        assert approve_res.status_code == 200
        assert approve_res.json()["status"] == "approved"

        # Verify audit log recorded approval
        audit_res = client.get("/audit", headers=AUTH_HEADERS)
        events = [a["event"] for a in audit_res.json()["items"]]
        assert "action_approved" in events

        # Verify chain integrity
        verify_res = client.get("/audit/verify", headers=AUTH_HEADERS)
        assert verify_res.status_code == 200
        assert verify_res.json()["valid"] is True


def test_kill_switch_blocks_write_actions():
    """Verify emergency kill switch halts all agent writes."""
    with TestClient(app) as client:
        # Activate kill switch
        ks_res = client.post("/policy/kill-switch", headers=AUTH_HEADERS, json={"active": True})
        assert ks_res.status_code == 200
        assert ks_res.json()["kill_switch"] is True

        # Attempt agent message that writes
        res = client.post("/agent/message", headers=AUTH_HEADERS, json={"message": "Invoice TestClient 5 hours at $50"})
        assert res.status_code == 200
        assert "blocked" in res.json()["final_message"].lower()

        # Deactivate kill switch
        client.post("/policy/kill-switch", headers=AUTH_HEADERS, json={"active": False})

