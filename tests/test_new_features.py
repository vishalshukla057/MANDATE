import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.config import settings

client = TestClient(app)
AUTH_HEADERS = {"X-API-Key": settings.API_KEY}


def test_mcp_tools_list():
    res = client.get("/mcp/tools")
    assert res.status_code == 200
    data = res.json()
    assert "tools" in data
    assert len(data["tools"]) > 0
    # Verify MCP protocol format
    first_tool = data["tools"][0]
    assert "name" in first_tool
    assert "description" in first_tool
    assert "inputSchema" in first_tool
    assert data["protocol_version"] == "2024-11-05"


def test_mcp_tool_call():
    # Invoke lookup_client via MCP
    payload = {
        "name": "lookup_client",
        "arguments": {"name": "Acme"}
    }
    res = client.post("/mcp/tools/call", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert "content" in data
    assert len(data["content"]) > 0
    assert data["content"][0]["type"] == "text"
    assert data["isError"] is False


def test_mcp_sse_stream():
    res = client.get("/mcp/sse")
    assert res.status_code == 200
    assert "text/event-stream" in res.headers["content-type"]
    assert "endpoint" in res.text


def test_audio_ingestion():
    payload = {
        "filename": "voice_memo_01.wav",
        "transcription_override": "Invoice Starlight Media $4,500.00 for brand strategy sprint due in 14 days"
    }
    res = client.post("/agent/ingest/audio", json=payload, headers=AUTH_HEADERS)
    assert res.status_code == 200
    data = res.json()
    assert data["filename"] == "voice_memo_01.wav"
    assert data["transcription_confidence"] > 0.9
    assert data["parsed"]["total"] == 4500.0


def test_undo_window_lifecycle():
    # Create or retrieve an action to stage
    from app.db import SessionLocal
    from app.models.agent import AgentAction
    db = SessionLocal()
    action = AgentAction(
        tenant_id=settings.DEFAULT_TENANT_ID,
        kind="reminder_send",
        tool_name="send_reminder",
        args={"invoice_number": "INV-1002", "tone": "firm"},
        risk_tier="medium",
        status="pending_approval",
        reasoning="Test reminder staging",
        signals={},
        policy_decision={"decision": "allow"},
    )
    db.add(action)
    db.commit()
    db.refresh(action)
    action_id = action.id
    db.close()

    # Stage action in 5-minute undo window
    res_stage = client.post(f"/agent/actions/{action_id}/stage", json={"grace_seconds": 300}, headers=AUTH_HEADERS)
    assert res_stage.status_code == 200
    assert res_stage.json()["status"] == "staged"

    # List staged actions
    res_list = client.get("/agent/actions/staged", headers=AUTH_HEADERS)
    assert res_list.status_code == 200
    staged_ids = [a["action_id"] for a in res_list.json()]
    assert action_id in staged_ids

    # Cancel action within undo window
    res_undo = client.post(
        f"/agent/actions/{action_id}/undo",
        json={"reason": "Operator revoked notice before dispatch"},
        headers=AUTH_HEADERS
    )
    assert res_undo.status_code == 200
    assert res_undo.json()["status"] == "cancelled"
    assert res_undo.json()["success"] is True


def test_carrier_verification_and_evidence_pack():
    # Seed demo data first to ensure dispute exists
    client.post("/simulate/seed-demo")

    disputes = client.get("/disputes", headers=AUTH_HEADERS).json()
    assert len(disputes) > 0
    dispute_id = disputes[0]["id"]

    # Verify tracking with carrier
    carrier_payload = {"tracking_number": "789234891023"}
    res_carrier = client.post(f"/disputes/{dispute_id}/carrier-verify", json=carrier_payload, headers=AUTH_HEADERS)
    assert res_carrier.status_code == 200
    carrier_data = res_carrier.json()
    assert carrier_data["carrier_proof"]["verified"] is True
    assert carrier_data["win_probability"] >= 0.85

    # Download evidence pack manifest
    res_download = client.get(f"/disputes/{dispute_id}/evidence-pack/download", headers=AUTH_HEADERS)
    assert res_download.status_code == 200
    manifest = res_download.json()
    assert "certification" in manifest
    assert "compiled_evidence" in manifest


def test_runway_simulator():
    res = client.get("/analytics/runway-simulator?monthly_burn_rate=15000&client_delay_days=21", headers=AUTH_HEADERS)
    assert res.status_code == 200
    data = res.json()
    assert "baseline_dso_days" in data
    assert "stressed_dso_days" in data
    assert data["dso_delta_days"] > 0
    assert len(data["client_risk_forecast"]) > 0


def test_fx_risk_and_hedging():
    # Seed demo data to ensure invoices exist
    client.post("/simulate/seed-demo")

    invoices = client.get("/invoices", headers=AUTH_HEADERS).json()["items"]
    assert len(invoices) > 0
    inv_id = invoices[0]["id"]

    res_fx = client.get(f"/invoices/{inv_id}/fx-risk", headers=AUTH_HEADERS)
    assert res_fx.status_code == 200
    data = res_fx.json()
    assert "invoice_currency" in data
    assert "current_usd_value" in data

    res_summary = client.get("/invoices/portfolio/fx-summary", headers=AUTH_HEADERS)
    assert res_summary.status_code == 200
    summary = res_summary.json()
    assert "total_portfolio_usd" in summary
    assert "currencies_represented" in summary

