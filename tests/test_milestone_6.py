import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.db import init_db, SessionLocal, engine, Base
from app.services.clock import ClockService
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


def test_analytics_summary_and_dso():
    """Verify executive summary metrics and Days Sales Outstanding calculation."""
    with TestClient(app) as client:
        # Seed demo dataset
        client.post("/simulate/seed-demo")

        res = client.get("/analytics/summary", headers=AUTH_HEADERS)
        assert res.status_code == 200
        summary = res.json()
        assert summary["total_outstanding"] > 0.0
        assert summary["total_overdue"] > 0.0
        assert summary["dso_days"] > 0.0
        assert len(summary["top_late_clients"]) > 0
        assert summary["top_late_clients"][0]["name"] == "Vanguard Logistics"


def test_cashflow_forecast_probabilistic_bands():
    """Verify probability-weighted cash flow modeling with contractor outflows."""
    with TestClient(app) as client:
        client.post("/simulate/seed-demo")

        res = client.get("/analytics/cashflow?days=30", headers=AUTH_HEADERS)
        assert res.status_code == 200
        forecast = res.json()
        assert "weekly_timeline" in forecast
        assert len(forecast["weekly_timeline"]) >= 4

        summary = forecast["summary"]
        assert summary["total_expected_inflow"] > 0.0
        assert summary["total_pessimistic_inflow"] <= summary["total_expected_inflow"]
        assert summary["total_optimistic_inflow"] >= summary["total_expected_inflow"]


def test_aging_buckets():
    """Verify 0-30, 31-60, 61-90, 90+ overdue aging buckets."""
    with TestClient(app) as client:
        client.post("/simulate/seed-demo")

        res = client.get("/analytics/aging", headers=AUTH_HEADERS)
        assert res.status_code == 200
        aging = res.json()["aging_buckets"]
        assert "0-30" in aging
        assert "31-60" in aging
        assert "61-90" in aging
        assert "90+" in aging
        assert aging["0-30"]["count"] >= 1


def test_constrained_natural_language_qa():
    """Verify constrained query plan execution for natural language financial analytics."""
    with TestClient(app) as client:
        client.post("/simulate/seed-demo")

        # Question 1: Who owes me money
        q1 = client.post("/analytics/ask", headers=AUTH_HEADERS, json={"question": "Who owes me the most money?"})
        assert q1.status_code == 200
        data1 = q1.json()
        assert data1["intent"] == "who_owes_money"
        assert len(data1["data"]) > 0
        assert "outstanding" in data1["answer"].lower()

        # Question 2: Cash flow forecast
        q2 = client.post("/analytics/ask", headers=AUTH_HEADERS, json={"question": "What is our cash flow forecast for the next 30 days?"})
        assert q2.status_code == 200
        data2 = q2.json()
        assert data2["intent"] == "cash_flow"
        assert data2["chart_hint"] == "bar"
        assert "projected net" in data2["answer"].lower()

        # Question 3: DSO
        q3 = client.post("/analytics/ask", headers=AUTH_HEADERS, json={"question": "What is our current DSO?"})
        assert q3.status_code == 200
        data3 = q3.json()
        assert data3["intent"] == "dso"
        assert "days" in data3["answer"].lower()


def test_jobs_management_and_daily_digest():
    """Verify background jobs trigger manually and generate daily digest."""
    with TestClient(app) as client:
        client.post("/simulate/seed-demo")

        # Run digest
        digest_res = client.post("/jobs/digest/run", headers=AUTH_HEADERS)
        assert digest_res.status_code == 200
        digest_data = digest_res.json()["result"]
        assert digest_data["overdue_count"] >= 1
        assert "Daily Digest:" in digest_data["digest"]

        # Recompute risk
        risk_res = client.post("/jobs/risk_recompute/run", headers=AUTH_HEADERS)
        assert risk_res.status_code == 200
        assert risk_res.json()["result"]["recomputed_clients"] == 6

