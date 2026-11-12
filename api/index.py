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

app = None
import_error = None

try:
    from app.main import app as fastapi_app
    app = fastapi_app
except Exception:
    import_error = traceback.format_exc()

if app is None:
    # Pure ASGI fallback using Python standard library (zero third-party dependencies required)
    async def app(scope, receive, send):
        if scope.get("type") == "lifespan":
            while True:
                msg = await receive()
                if msg["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif msg["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        elif scope.get("type") == "http":
            html = f"""<!DOCTYPE html>
<html>
<head><title>Collections Copilot - Diagnostics</title></head>
<body style="font-family: monospace; padding: 30px; background: #0f172a; color: #f87171;">
    <h2 style="color: #fb7185;">⚠️ Serverless Initialization Error on Vercel</h2>
    <p style="color: #94a3b8;">Python: {sys.version}</p>
    <p style="color: #94a3b8;">Directory: {os.getcwd()}</p>
    <pre style="background: #1e293b; color: #f1f5f9; padding: 20px; border-radius: 8px; overflow: auto; border: 1px solid #334155;">{import_error}</pre>
</body>
</html>"""
            body = html.encode("utf-8")
            await send({
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    [b"content-type", b"text/html; charset=utf-8"],
                    [b"content-length", str(len(body)).encode("ascii")],
                ],
            })
            await send({
                "type": "http.response.body",
                "body": body,
            })
