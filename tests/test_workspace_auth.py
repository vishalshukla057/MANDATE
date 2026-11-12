import tempfile
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.agent.tools import TOOL_REGISTRY
from app.config import settings
from app.db import Base, get_db
from app.main import app
from app.paypal.client import reset_fake_gateway
from app.services.clock import ClockService


@pytest.fixture(autouse=True)
def isolate_test_db(monkeypatch):
    monkeypatch.setattr(settings, "PAYPAL_MODE", "fake")
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "mock_gemini_key")
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "mock_anthropic_key")
    monkeypatch.setattr("app.main.init_db", lambda: None)

    tmp_dir = tempfile.mkdtemp()
    db_path = Path(tmp_dir) / "test_ws.db"
    test_engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False}, future=True)
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    from app.models import (  # noqa: F401
        agent, audit, client as client_m, contractor, dispute,
        input_record, invoice, job, offer, payment, payout, policy, reminder, user, webhook,
    )
    Base.metadata.create_all(bind=test_engine)

    def override_get_db():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    reset_fake_gateway()
    ClockService.reset()
    yield
    app.dependency_overrides.clear()
    test_engine.dispose()
    ClockService.reset()


client = TestClient(app)


def test_gemini_tool_declarations_valid():
    for name, tool in TOOL_REGISTRY.items():
        decl = tool.to_gemini_declaration()
        assert decl["name"] == name
        assert "default" not in str(decl["parameters"])


def test_signup_empty_by_default_and_isolated():
    res_a = client.post(
        "/auth/signup",
        json={
            "full_name": "Alice Founder",
            "workspace_name": "Alice Studio",
            "email": "alice@studio.io",
            "password": "password123",
        },
    )
    assert res_a.status_code == 201
    data_a = res_a.json()
    assert data_a["mode"] == "authenticated"
    assert data_a["seeded"] is None
    headers_a = {"X-Workspace-Token": data_a["token"]}

    # Default personal workspace starts completely empty
    assert client.get("/clients", headers=headers_a).json() == []
    assert client.get("/invoices", headers=headers_a).json()["total"] == 0
    assert client.get("/analytics/summary", headers=headers_a).json()["total_outstanding"] == 0.0

    # User A adds their own client and invoice and records payment
    cli_res = client.post(
        "/clients",
        headers=headers_a,
        json={"name": "Northstar Corp", "email": "ap@northstar.io", "currency": "USD", "default_terms_days": 15},
    )
    assert cli_res.status_code == 201
    cli_id = cli_res.json()["id"]

    inv_res = client.post(
        "/invoices",
        headers=headers_a,
        json={
            "client_id": cli_id,
            "currency": "USD",
            "terms_days": 15,
            "line_items": [{"name": "Design Retainer", "quantity": 1, "unit_amount": 750.0}],
        },
    )
    assert inv_res.status_code == 201
    inv_id = inv_res.json()["id"]

    assert client.get("/analytics/summary", headers=headers_a).json()["total_outstanding"] == 750.0

    pay_res = client.post(f"/invoices/{inv_id}/payments", headers=headers_a, json={"amount": 750.0, "method": "PAYPAL"})
    assert pay_res.status_code == 200
    assert pay_res.json()["invoice_status"] == "paid"
    assert client.get("/analytics/summary", headers=headers_a).json()["total_outstanding"] == 0.0
    assert client.get("/audit/verify", headers=headers_a).json()["valid"] is True

    # User B signs up with seed_mock_data=True; isolated from User A
    res_b = client.post(
        "/auth/signup",
        json={
            "full_name": "Bob Ops",
            "workspace_name": "Bob Agency",
            "email": "bob@agency.io",
            "password": "password123",
            "seed_mock_data": True,
        },
    )
    assert res_b.status_code == 201
    data_b = res_b.json()
    assert data_b["seeded"]["clients"] > 0
    headers_b = {"X-Workspace-Token": data_b["token"]}
    b_client_names = [c["name"] for c in client.get("/clients", headers=headers_b).json()]
    assert "Northstar Corp" not in b_client_names

    # User A can also seed mock data later or clear their workspace
    assert client.post("/workspace/clear", headers=headers_a).status_code == 200
    assert client.get("/clients", headers=headers_a).json() == []
    assert client.post("/workspace/seed-mock", headers=headers_a, json={"reset": False}).status_code == 200
    assert len(client.get("/clients", headers=headers_a).json()) > 0


def test_login_validation():
    signup = client.post(
        "/auth/signup",
        json={
            "full_name": "Cara Fin",
            "workspace_name": "Cara LLC",
            "email": "cara@llc.io",
            "password": "secret99",
        },
    )
    assert signup.status_code == 201
    tok_init = signup.json()["token"]
    # Save a client in Cara's workspace
    client.post(
        "/clients",
        headers={"X-Workspace-Token": tok_init},
        json={"name": "Saved Client Co", "email": "ap@saved.io"},
    )

    # Verify password is stored hashed (not plaintext)
    from app.models.user import WorkspaceUser
    db_gen = app.dependency_overrides[get_db]()
    db_sess = next(db_gen)
    u = db_sess.query(WorkspaceUser).filter(WorkspaceUser.email == "cara@llc.io").first()
    assert u is not None
    assert u.password_hash != "secret99"
    assert "$" in u.password_hash and len(u.password_hash.split("$")[1]) == 64

    bad = client.post("/auth/login", json={"email": "cara@llc.io", "password": "wrong"})
    assert bad.status_code == 401
    assert "Wrong password" in bad.json()["detail"]
    assert "token" not in bad.json()

    ok = client.post("/auth/login", json={"email": "cara@llc.io", "password": "secret99"})
    assert ok.status_code == 200
    tok = ok.json()["token"]
    me = client.get("/auth/me", headers={"X-Workspace-Token": tok}).json()
    assert me["mode"] == "authenticated"
    assert me["user"]["email"] == "cara@llc.io"
    # Previous work remains stored after logging back in
    clients = client.get("/clients", headers={"X-Workspace-Token": tok}).json()
    assert [c["name"] for c in clients] == ["Saved Client Co"]


def test_explore_mode_without_signup():
    res = client.post("/auth/explore")
    assert res.status_code == 200
    data = res.json()
    assert data["mode"] == "explore"
    assert data["seeded"]["clients"] > 0
    headers = {"X-Workspace-Token": data["token"]}

    assert len(client.get("/clients", headers=headers).json()) > 0
    assert client.get("/invoices", headers=headers).json()["total"] > 0
    assert len(client.get("/approvals?status=pending_approval", headers=headers).json()) > 0
    assert len(client.get("/disputes", headers=headers).json()) > 0
