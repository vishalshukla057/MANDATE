import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any, Dict, Optional

from fastapi import Request
from app.config import settings

DEMO_TENANT_ID = "tenant_demo"
TOKEN_TTL_SECONDS = 60 * 60 * 24 * 7  # 7 days


def _signing_secret() -> bytes:
    raw = f"mandate-ws-secret::{settings.API_KEY}".encode("utf-8")
    return hashlib.sha256(raw).digest()


def hash_password(password: str) -> str:
    salt = os.urandom(16).hex()
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000).hex()
    return f"{salt}${dk}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, expected_dk = stored.split("$", 1)
    except ValueError:
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000).hex()
    return hmac.compare_digest(dk, expected_dk)


def create_session_token(
    user_id: str,
    tenant_id: str,
    email: str,
    full_name: str,
    workspace_name: str,
) -> str:
    payload = {
        "uid": user_id,
        "tid": tenant_id,
        "email": email,
        "name": full_name,
        "ws": workspace_name,
        "exp": int(time.time()) + TOKEN_TTL_SECONDS,
    }
    raw_b64 = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8")).decode("ascii")
    sig = hmac.new(_signing_secret(), raw_b64.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{raw_b64}.{sig}"


def verify_session_token(token: Optional[str]) -> Optional[Dict[str, Any]]:
    if not token or "." not in token:
        return None
    try:
        raw_b64, sig = token.rsplit(".", 1)
        expected_sig = hmac.new(_signing_secret(), raw_b64.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected_sig):
            return None
        padded = raw_b64 + "=" * (-len(raw_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        return payload
    except Exception:
        return None


def extract_token_from_request(request: Request) -> Optional[str]:
    ws_tok = request.headers.get("X-Workspace-Token")
    if ws_tok:
        return ws_tok.strip()
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


def get_tenant_id(request: Request) -> str:
    """Resolve current workspace tenant_id from signed token, explore header, or default."""
    claims = verify_session_token(extract_token_from_request(request))
    if claims and claims.get("tid"):
        return str(claims["tid"])
    if request.headers.get("X-Workspace-Mode", "").lower() == "explore":
        return DEMO_TENANT_ID
    return settings.DEFAULT_TENANT_ID

