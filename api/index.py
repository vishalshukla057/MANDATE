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

try:
    from app.main import app
except Exception as e:
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse

    app = FastAPI(title="Diagnostics App")
    exc_info = traceback.format_exc()

    @app.api_route("/{path_name:path}", methods=["GET", "POST", "PUT", "DELETE"])
    async def report_exception(path_name: str = ""):
        return HTMLResponse(
            f"<html><head><title>Vercel Deployment Diagnostics</title></head>"
            f"<body style='font-family: monospace; padding: 30px; background: #0f172a; color: #f87171;'>"
            f"<h2 style='color: #fb7185;'>⚠️ Startup Exception Detected on Vercel</h2>"
            f"<p style='color: #94a3b8;'>Path: /{path_name} | Python: {sys.version}</p>"
            f"<pre style='background: #1e293b; color: #f1f5f9; padding: 20px; border-radius: 8px; overflow: auto;'>{exc_info}</pre>"
            f"</body></html>",
            status_code=200
        )
