import os
import sys
import traceback

# Ensure root paths are in sys.path
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
for p in [current_dir, parent_dir, os.getcwd(), "/var/task"]:
    if os.path.exists(p) and p not in sys.path:
        sys.path.insert(0, p)

if os.environ.get("VERCEL") or os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
    os.environ["DATABASE_URL"] = "sqlite:////tmp/copilot.db"

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse
from app.main import app, serve_ui

@app.middleware("http")
async def vercel_path_rewrite_middleware(request: Request, call_next):
    # Check headers for the real incoming path
    for header in ["x-matched-path", "x-vercel-matched-path", "x-forwarded-uri", "x-original-uri"]:
        matched = request.headers.get(header)
        if matched and matched not in ["/api/index.py", "/api/index", "/api"]:
            request.scope["path"] = matched
            break
    return await call_next(request)

@app.get("/api/index.py", response_class=HTMLResponse, tags=["Vercel Entry"])
@app.get("/api/index", response_class=HTMLResponse, tags=["Vercel Entry"])
@app.get("/api", response_class=HTMLResponse, tags=["Vercel Entry"])
async def serve_vercel_entry(request: Request):
    return await serve_ui(request)
