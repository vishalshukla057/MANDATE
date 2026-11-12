import os
import sys
import traceback

root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

if os.environ.get("VERCEL") or os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
    os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/copilot.db")

try:
    from app.main import app
    try:
        from mangum import Mangum
        handler = Mangum(app)
    except Exception:
        handler = app

except Exception as e:
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse

    app = FastAPI()
    err_tb = traceback.format_exc()

    @app.api_route("/{path_name:path}", methods=["GET", "POST", "PUT", "DELETE"])
    async def catch_all(path_name: str = ""):
        return HTMLResponse(
            f"<html><body style='font-family:monospace;padding:24px;background:#fff5f5;color:#c53030;'>"
            f"<h2>Serverless Startup Exception</h2>"
            f"<pre style='background:#fff;padding:16px;border:1px solid #feb2b2;border-radius:8px;'>{err_tb}</pre>"
            f"</body></html>",
            status_code=500
        )
    handler = app
