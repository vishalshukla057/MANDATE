import os
import sys

root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

if os.environ.get("VERCEL") or os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
    os.environ["DATABASE_URL"] = "sqlite:////tmp/copilot.db"

from app.main import app

@app.get("/debug-env")
def debug_env():
    return {
        "status": "online",
        "python_version": sys.version,
        "cwd": os.getcwd(),
        "database_url": os.environ.get("DATABASE_URL"),
        "is_vercel": bool(os.environ.get("VERCEL")),
    }
