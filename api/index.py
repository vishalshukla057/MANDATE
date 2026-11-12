import os
import sys

# Ensure repository root is on sys.path
root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

# On Vercel serverless environment, route SQLite to writable /tmp
if os.environ.get("VERCEL"):
    os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/copilot.db")

from app.db import init_db, SessionLocal
from app.models.client import Client
from app.main import app

# Ensure tables are initialized on cold start
init_db()
try:
    with SessionLocal() as db:
        if db.query(Client).count() == 0:
            from app.api.simulate import seed_demo_dataset
            seed_demo_dataset(db=db)
except Exception as e:
    pass
