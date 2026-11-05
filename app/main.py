import hmac
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Request, Response, Security, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.security import APIKeyHeader
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings
from app.db import init_db
from app.services.clock import ClockService
from app.paypal.client import get_paypal_gateway, PayPalError
from app.api import (
    invoices, clients, webhooks, agent, approvals,
    feed, policy_audit, simulate, contractors, payouts, disputes,
    analytics, jobs
)


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        correlation_id = request.headers.get("X-Correlation-ID") or f"cid-{uuid.uuid4().hex[:12]}"
        request.state.correlation_id = correlation_id
        start_time = time.time()
        
        try:
            response = await call_next(request)
        except Exception as exc:
            duration_ms = (time.time() - start_time) * 1000
            err_details = getattr(exc, "details", None)
            code = getattr(exc, "name", exc.__class__.__name__)
            status_code = getattr(exc, "status_code", 500)
            
            return JSONResponse(
                status_code=status_code,
                headers={"X-Correlation-ID": correlation_id},
                content={
                    "error": {
                        "code": code,
                        "message": str(exc),
                        "details": err_details,
                        "correlation_id": correlation_id,
                    }
                },
            )

        response.headers["X-Correlation-ID"] = correlation_id
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: initialize database tables
    init_db()
    yield
    # Shutdown logic if needed


app = FastAPI(
    title="Collections Copilot API",
    description="Policy-bound, explainable, and auditable AI agent for PayPal collections, invoicing, and risk management.",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(CorrelationIdMiddleware)

# Auth dependency
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_api_key(request: Request, api_key: Optional[str] = Security(api_key_header)):
    # Exempt public routes: /health, /webhooks/paypal, root docs & UI
    public_paths = ["/health", "/docs", "/redoc", "/openapi.json", "/webhooks/paypal", "/", "/ui"]
    if request.url.path in public_paths or request.url.path.startswith("/simulate/"):
        return api_key

    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-API-Key header",
        )

    # Constant-time comparison
    if not hmac.compare_digest(api_key.encode("utf-8"), settings.API_KEY.encode("utf-8")):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API key",
        )
    return api_key


# Routes
app.include_router(invoices.router, dependencies=[Security(verify_api_key)])
app.include_router(clients.router, dependencies=[Security(verify_api_key)])
app.include_router(agent.router, dependencies=[Security(verify_api_key)])
app.include_router(approvals.router, dependencies=[Security(verify_api_key)])
app.include_router(feed.router, dependencies=[Security(verify_api_key)])
app.include_router(policy_audit.router, dependencies=[Security(verify_api_key)])
app.include_router(contractors.router, dependencies=[Security(verify_api_key)])
app.include_router(payouts.router, dependencies=[Security(verify_api_key)])
app.include_router(disputes.router, dependencies=[Security(verify_api_key)])
app.include_router(analytics.router, dependencies=[Security(verify_api_key)])
app.include_router(jobs.router, dependencies=[Security(verify_api_key)])
app.include_router(simulate.router)  # Simulation tools for hackathon demo
app.include_router(webhooks.router)  # Public, signature verified


@app.get("/health", tags=["System"])
async def health_check():
    return {
        "status": "healthy",
        "service": "Collections Copilot",
        "app_env": settings.APP_ENV,
        "paypal_mode": settings.PAYPAL_MODE,
        "clock_time": ClockService.now().isoformat(),
        "time_offset_days": ClockService.get_offset_days(),
        "kill_switch": settings.KILL_SWITCH,
    }


@app.get("/", response_class=HTMLResponse, tags=["UI"])
@app.get("/ui", response_class=HTMLResponse, tags=["UI"])
async def serve_ui():
    """Serves the Google Stitch Sovereign Heritage frontend UI."""
    ui_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend", "code.html")
    if os.path.exists(ui_path):
        with open(ui_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse("<h2>Collections Copilot UI not found</h2>", status_code=404)

