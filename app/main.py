import hmac
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Request, Response, Security, HTTPException, status, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.security import APIKeyHeader
from starlette.middleware.base import BaseHTTPMiddleware
from sqlalchemy.orm import Session

from app.config import settings
from app.db import init_db, get_db
from app.models.invoice import Invoice
from app.models.client import Client
from app.services.clock import ClockService
from app.paypal.client import get_paypal_gateway, PayPalError
from app.api import (
    invoices, clients, webhooks, agent, approvals,
    feed, policy_audit, simulate, contractors, payouts, disputes,
    analytics, jobs, mcp
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
    public_paths = [
        "/health", "/docs", "/redoc", "/openapi.json", "/webhooks/paypal",
        "/", "/ui", "/ui/light", "/ui/dark"
    ]
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
app.include_router(mcp.router)  # Model Context Protocol (MCP) tool server
app.include_router(simulate.router)  # Simulation tools for hackathon demo
app.include_router(webhooks.router)  # Public, signature verified


@app.get("/pay/{id}", response_class=HTMLResponse, tags=["Payer View"])
@app.get("/invoices/{id}/view", response_class=HTMLResponse, tags=["Payer View"])
def public_payer_invoice_view(id: str, db: Session = Depends(get_db)):
    """Serves an authentic PayPal Sandbox Payer Checkout & Invoice Receipt interface without requiring internal API headers."""
    invoice = db.query(Invoice).filter((Invoice.id == id) | (Invoice.paypal_invoice_id == id)).first()
    if not invoice:
        return HTMLResponse("""
            <!DOCTYPE html><html><head><title>PayPal Sandbox - Not Found</title>
            <script src="https://cdn.tailwindcss.com"></script></head>
            <body class="bg-slate-50 flex items-center justify-center min-h-screen">
                <div class="bg-white p-8 rounded-2xl shadow-xl max-w-md text-center">
                    <h2 class="text-2xl font-bold text-slate-800 mb-2">Invoice Not Found</h2>
                    <p class="text-slate-500 mb-6 text-sm">We couldn't locate this invoice record in the system.</p>
                    <a href="/" class="px-5 py-2.5 bg-[#0070ba] text-white font-bold text-sm rounded-lg">Return to Console</a>
                </div>
            </body></html>
        """, status_code=404)

    client = db.query(Client).filter(Client.id == invoice.client_id).first()
    client_name = client.name if client else "Enterprise Client"
    client_email = client.email if client else "billing@example.com"

    line_items_html = ""
    items = invoice.line_items or [{"description": "Professional Services", "quantity": 1, "unit": "item", "unit_price": invoice.total}]
    for it in items:
        qty = it.get("quantity", 1)
        rate = it.get("unit_price", 0.0)
        tot = round(qty * rate, 2)
        desc = it.get("description", "Item")
        unit = it.get("unit", "")
        line_items_html += f"""
            <tr class="border-b border-slate-100">
                <td class="py-3 px-4 text-slate-800 font-medium">{desc}</td>
                <td class="py-3 px-4 text-center text-slate-600 font-mono">{qty} {unit}</td>
                <td class="py-3 px-4 text-right text-slate-600 font-mono">${rate:,.2f}</td>
                <td class="py-3 px-4 text-right text-slate-900 font-bold font-mono">${tot:,.2f}</td>
            </tr>
        """

    is_paid = (invoice.status == "paid")
    status_color = "bg-emerald-100 text-emerald-800 border-emerald-300" if is_paid else "bg-amber-100 text-amber-800 border-amber-300"
    issue_date_str = invoice.issue_date.strftime("%b %d, %Y") if invoice.issue_date else "Today"
    due_date_str = invoice.due_date.strftime("%b %d, %Y") if invoice.due_date else "Net 15"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>PayPal Sandbox - Invoice #{invoice.paypal_invoice_number}</title>
    <link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='6' fill='%23003087'/%3E%3Cpath d='M9 6h7.5c4.5 0 6.5 2.2 5.5 6.5-1.2 5.2-4.5 7.5-9 7.5H11l-1.5 6H7L9 6z' fill='%230079C1'/%3E%3Cpath d='M13 10h6.5c3.5 0 5 1.7 4.2 5.2-.9 4.2-3.5 6-7 6H14.5l-1.2 4.8H11l2-16z' fill='%23FFC439'/%3E%3C/svg%3E"/>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined" rel="stylesheet"/>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@500;700&display=swap');
        body {{ font-family: 'Inter', sans-serif; }}
    </style>
</head>
<body class="bg-[#f5f7fa] text-slate-800 min-h-screen flex flex-col justify-between">

    <!-- PayPal Sandbox Top Banner -->
    <header class="bg-[#003087] text-white px-6 py-4 shadow-md sticky top-0 z-30">
        <div class="max-w-4xl mx-auto flex items-center justify-between">
            <div class="flex items-center gap-3">
                <div class="flex items-center font-black tracking-tighter text-2xl select-none">
                    <span class="text-white">Pay</span><span class="text-[#0079c1]">Pal</span>
                </div>
                <span class="px-2.5 py-0.5 rounded-full bg-[#ffc439] text-[#00174b] text-[11px] font-black uppercase tracking-wider">Sandbox Test</span>
            </div>
            <a href="/" class="text-xs text-sky-200 hover:text-white flex items-center gap-1 transition-colors font-medium">
                <span class="material-symbols-outlined text-[16px]">arrow_back</span>
                <span>Back to Merchant Console</span>
            </a>
        </div>
    </header>

    <!-- Main Invoice Card -->
    <main class="max-w-4xl w-full mx-auto p-4 sm:p-8 flex-1">
        <div class="bg-white rounded-3xl shadow-[0_10px_40px_rgba(0,0,0,0.06)] border border-slate-200/80 overflow-hidden">
            
            <!-- Invoice Header Bar -->
            <div class="bg-gradient-to-r from-[#001c48] to-[#003087] text-white p-6 sm:p-8 flex flex-col sm:flex-row sm:items-center justify-between gap-4">
                <div>
                    <div class="flex items-center gap-2 mb-1">
                        <span class="text-xs font-mono uppercase tracking-widest text-sky-300">Invoice Reference</span>
                        <span class="px-2.5 py-0.5 rounded-full text-[10px] font-black uppercase tracking-wider border {status_color}">{invoice.status.upper()}</span>
                    </div>
                    <h1 class="text-3xl font-extrabold tracking-tight">{invoice.paypal_invoice_number}</h1>
                    <p class="text-xs text-sky-200/80 font-mono mt-1">PayPal Internal ID: {invoice.paypal_invoice_id}</p>
                </div>
                <div class="text-left sm:text-right">
                    <span class="text-xs text-sky-200/80 uppercase tracking-wider block">Amount Due</span>
                    <div class="text-3xl font-black text-[#ffc439] font-mono">${invoice.amount_due:,.2f} <span class="text-sm text-white font-sans">{invoice.currency}</span></div>
                </div>
            </div>

            <!-- Party Details Grid -->
            <div class="p-6 sm:p-8 grid grid-cols-1 sm:grid-cols-2 gap-8 border-b border-slate-100">
                <div>
                    <span class="text-xs font-bold uppercase tracking-wider text-slate-400 block mb-2">Merchant (Invoicer)</span>
                    <div class="font-bold text-slate-900 text-base">Collections Copilot Merchant</div>
                    <div class="text-slate-500 text-sm">{settings.PAYPAL_MERCHANT_EMAIL}</div>
                    <div class="text-xs text-slate-400 mt-2 font-mono">PayPal Verified Business Sandbox</div>
                </div>
                <div>
                    <span class="text-xs font-bold uppercase tracking-wider text-slate-400 block mb-2">Billed To (Payer)</span>
                    <div class="font-bold text-slate-900 text-base">{client_name}</div>
                    <div class="text-slate-500 text-sm">{client_email}</div>
                    <div class="text-xs text-slate-500 mt-2 flex items-center gap-4">
                        <span>Issued: <strong>{issue_date_str}</strong></span>
                        <span>Due: <strong class="text-[#0070ba]">{due_date_str}</strong></span>
                    </div>
                </div>
            </div>

            <!-- Line Items Table -->
            <div class="p-6 sm:p-8">
                <h3 class="text-xs font-bold uppercase tracking-wider text-slate-400 mb-4">Itemized Services</h3>
                <div class="overflow-x-auto rounded-xl border border-slate-200/70">
                    <table class="w-full text-left text-sm">
                        <thead class="bg-slate-50 text-slate-500 text-xs font-semibold uppercase tracking-wider border-b border-slate-200/70">
                            <tr>
                                <th class="py-3 px-4">Description</th>
                                <th class="py-3 px-4 text-center">Quantity</th>
                                <th class="py-3 px-4 text-right">Unit Price</th>
                                <th class="py-3 px-4 text-right">Amount</th>
                            </tr>
                        </thead>
                        <tbody>
                            {line_items_html}
                        </tbody>
                    </table>
                </div>

                <!-- Totals Section -->
                <div class="mt-6 flex flex-col items-end gap-1.5 text-sm font-mono">
                    <div class="flex justify-between w-64 text-slate-600">
                        <span>Subtotal:</span>
                        <span>${invoice.subtotal:,.2f}</span>
                    </div>
                    {f'<div class="flex justify-between w-64 text-emerald-600"><span>Discount:</span><span>-${invoice.discount_total:,.2f}</span></div>' if invoice.discount_total > 0 else ''}
                    <div class="flex justify-between w-64 text-slate-900 font-bold text-lg pt-2 border-t border-slate-200">
                        <span class="font-sans">Total:</span>
                        <span>${invoice.total:,.2f} {invoice.currency}</span>
                    </div>
                </div>
            </div>

            <!-- Action & Payment Simulation Footer -->
            <div class="p-6 sm:p-8 bg-slate-50 border-t border-slate-100 flex flex-col sm:flex-row items-center justify-between gap-4">
                <div class="flex items-center gap-2 text-xs text-slate-500">
                    <span class="material-symbols-outlined text-[18px] text-emerald-600">lock</span>
                    <span>256-bit Encrypted Mock Sandbox Checkout</span>
                </div>
                <div class="flex items-center gap-3">
                    <button onclick="window.print()" type="button" class="px-4 py-2.5 rounded-xl bg-white hover:bg-slate-100 text-slate-700 border border-slate-300 font-semibold text-xs transition-all shadow-sm cursor-pointer">
                        Print Invoice
                    </button>
                    {
                        '<div class="px-5 py-2.5 rounded-xl bg-emerald-100 text-emerald-800 font-bold text-xs flex items-center gap-1.5"><span class="material-symbols-outlined text-[16px]">check_circle</span><span>Paid in Full</span></div>'
                        if is_paid else
                        f'<button id="simulate-pay-btn" onclick="simulatePayment(\'{invoice.id}\')" type="button" class="px-6 py-2.5 rounded-xl bg-[#ffc439] hover:bg-[#f5b324] text-[#00174b] font-extrabold text-xs uppercase tracking-wider flex items-center gap-2 shadow-md transition-all active:scale-95 cursor-pointer"><span class="material-symbols-outlined text-[18px]">payments</span><span>Pay ${invoice.amount_due:,.2f} with PayPal Sandbox</span></button>'
                    }
                </div>
            </div>

        </div>
    </main>

    <!-- Footer -->
    <footer class="bg-white border-t border-slate-200 py-6 text-center text-xs text-slate-400">
        <p>© 2025 PayPal Inc. • Autonomous Invoicing Copilot Sandbox</p>
    </footer>

    <script>
        async function simulatePayment(id) {{
            const btn = document.getElementById('simulate-pay-btn');
            if (btn) {{
                btn.disabled = true;
                btn.innerText = 'Processing via PayPal Sandbox...';
            }}
            try {{
                const res = await fetch('/simulate/client-pays', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ invoice_id: id, mode: 'full' }})
                }});
                if (res.ok) {{
                    alert('✓ Payment settled successfully via PayPal Sandbox! Status flipped to PAID.');
                    window.location.reload();
                }} else {{
                    const err = await res.json();
                    alert('Payment Error: ' + (err.detail || 'Could not process payment'));
                    if (btn) btn.disabled = false;
                }}
            }} catch (e) {{
                alert('Network Error: ' + e.message);
                if (btn) btn.disabled = false;
            }}
        }}
    </script>
</body>
</html>
"""
    return HTMLResponse(content=html)


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


def _resolve_ui_path(folder: str, filename: str) -> str:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    p1 = os.path.join(root, folder, filename)
    if os.path.exists(p1):
        return p1
    p2 = os.path.join(os.getcwd(), folder, filename)
    if os.path.exists(p2):
        return p2
    p3 = os.path.join("/var/task", folder, filename)
    if os.path.exists(p3):
        return p3
    return p1


@app.get("/", response_class=HTMLResponse, tags=["UI"])
@app.get("/ui", response_class=HTMLResponse, tags=["UI"])
async def serve_ui(request: Request, theme: Optional[str] = None):
    """Serves the frontend UI (defaulting cleanly to light mode, clearing any stale dark mode cookies)."""
    if theme and theme.lower() == "dark":
        ui_path = _resolve_ui_path("frontend", "code.html")
    else:
        ui_path = _resolve_ui_path("frontend_light", "code.html")

    if os.path.exists(ui_path):
        with open(ui_path, "r", encoding="utf-8") as f:
            resp = HTMLResponse(content=f.read())
            if theme:
                resp.set_cookie("copilot_theme", theme, max_age=31536000, path="/")
            else:
                resp.set_cookie("copilot_theme", "light", max_age=31536000, path="/")
            return resp
    return HTMLResponse("<h2>Collections Copilot UI not found</h2>", status_code=404)


@app.get("/ui/light", response_class=HTMLResponse, tags=["UI"])
async def serve_ui_light():
    """Serves the PayPal Global Design System Light Mode UI."""
    ui_path = _resolve_ui_path("frontend_light", "code.html")
    if os.path.exists(ui_path):
        with open(ui_path, "r", encoding="utf-8") as f:
            resp = HTMLResponse(content=f.read())
            resp.set_cookie("copilot_theme", "light", max_age=31536000, path="/")
            return resp
    return HTMLResponse("<h2>Light Mode UI not found</h2>", status_code=404)


@app.get("/ui/dark", response_class=HTMLResponse, tags=["UI"])
async def serve_ui_dark():
    """Serves the Sovereign Heritage Dark Mode UI."""
    ui_path = _resolve_ui_path("frontend", "code.html")
    if os.path.exists(ui_path):
        with open(ui_path, "r", encoding="utf-8") as f:
            resp = HTMLResponse(content=f.read())
            resp.set_cookie("copilot_theme", "dark", max_age=31536000, path="/")
            return resp
    return HTMLResponse("<h2>Dark Mode UI not found</h2>", status_code=404)


@app.get("/privacy", response_class=HTMLResponse, tags=["Legal"])
async def serve_privacy():
    """Serves the Enterprise Privacy Policy."""
    path = _resolve_ui_path("frontend", "privacy.html")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse("<h2>Privacy Policy not found</h2>", status_code=404)


@app.get("/terms", response_class=HTMLResponse, tags=["Legal"])
async def serve_terms():
    """Serves the Enterprise Terms and Conditions."""
    path = _resolve_ui_path("frontend", "terms.html")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse("<h2>Terms of Service not found</h2>", status_code=404)


@app.get("/favicon.ico", tags=["System"])
async def serve_favicon():
    """Serves official SVG favicon."""
    svg_icon = """<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><rect width='32' height='32' rx='6' fill='%23003087'/><path d='M9 6h7.5c4.5 0 6.5 2.2 5.5 6.5-1.2 5.2-4.5 7.5-9 7.5H11l-1.5 6H7L9 6z' fill='%230079C1'/><path d='M13 10h6.5c3.5 0 5 1.7 4.2 5.2-.9 4.2-3.5 6-7 6H14.5l-1.2 4.8H11l2-16z' fill='%2300457C'/></svg>"""
    return Response(content=svg_icon, media_type="image/svg+xml")


@app.api_route("/{full_path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def catch_all_fallback(request: Request, full_path: str = ""):
    # If path is root, UI, or vercel entrypoint, serve UI directly
    if not full_path or full_path in ["ui", "index.html", "api/index.py", "api/index", "api"]:
        return await serve_ui(request)
    return JSONResponse({
        "msg": "catch_all_hit",
        "full_path": full_path,
        "scope_path": request.scope.get("path"),
        "headers": dict(request.headers),
    })


