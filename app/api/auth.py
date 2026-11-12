import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth_context import (
    DEMO_TENANT_ID,
    create_session_token,
    extract_token_from_request,
    get_tenant_id,
    hash_password,
    verify_password,
    verify_session_token,
)
from app.db import get_db
from app.models.user import WorkspaceUser
from app.services.workspace_seeder import clear_workspace_data, seed_workspace_data

router = APIRouter(tags=["Auth & Workspace"])


class SignUpRequest(BaseModel):
    full_name: str = Field(..., min_length=1, max_length=120)
    workspace_name: str = Field(..., min_length=1, max_length=120)
    email: str = Field(..., min_length=3, max_length=255)
    password: str = Field(..., min_length=6, max_length=128)
    seed_mock_data: bool = False


class LoginRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=255)
    password: str = Field(..., min_length=1, max_length=128)


class SeedWorkspaceRequest(BaseModel):
    reset: bool = False


@router.post("/auth/signup", status_code=status.HTTP_201_CREATED)
def signup_workspace(payload: SignUpRequest, db: Session = Depends(get_db)):
    """Create a personalized user & isolated workspace (empty by default, optional mock seed)."""
    email_norm = payload.email.strip().lower()
    if "@" not in email_norm or "." not in email_norm.split("@", 1)[-1]:
        raise HTTPException(status_code=400, detail="Please enter a valid email address.")

    existing = db.query(WorkspaceUser).filter(WorkspaceUser.email == email_norm).first()
    if existing:
        raise HTTPException(status_code=409, detail="An account with this email already exists. Please sign in.")

    tenant_id = f"ws_{uuid.uuid4().hex[:12]}"
    user = WorkspaceUser(
        email=email_norm,
        full_name=payload.full_name.strip(),
        workspace_name=payload.workspace_name.strip(),
        tenant_id=tenant_id,
        password_hash=hash_password(payload.password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    seeded_info = None
    if payload.seed_mock_data:
        seeded_info = seed_workspace_data(db, tenant_id=user.tenant_id, reset=False)

    token = create_session_token(
        user_id=user.id,
        tenant_id=user.tenant_id,
        email=user.email,
        full_name=user.full_name,
        workspace_name=user.workspace_name,
    )
    return {
        "token": token,
        "mode": "authenticated",
        "user": {
            "id": user.id,
            "email": user.email,
            "full_name": user.full_name,
            "workspace_name": user.workspace_name,
            "tenant_id": user.tenant_id,
        },
        "seeded": seeded_info,
    }


@router.post("/auth/login")
def login_workspace(payload: LoginRequest, db: Session = Depends(get_db)):
    """Authenticate an existing workspace user with email and password."""
    email_norm = payload.email.strip().lower()
    user = db.query(WorkspaceUser).filter(WorkspaceUser.email == email_norm).first()
    if not user:
        raise HTTPException(status_code=401, detail="No account found for that email. Please sign up first.")
    if not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Wrong password. Please check your password and try again.")

    token = create_session_token(
        user_id=user.id,
        tenant_id=user.tenant_id,
        email=user.email,
        full_name=user.full_name,
        workspace_name=user.workspace_name,
    )
    return {
        "token": token,
        "mode": "authenticated",
        "user": {
            "id": user.id,
            "email": user.email,
            "full_name": user.full_name,
            "workspace_name": user.workspace_name,
            "tenant_id": user.tenant_id,
        },
    }


@router.post("/auth/explore")
def start_explore_session(reset: bool = False, db: Session = Depends(get_db)):
    """Start a guest login Explore session backed by a dedicated seeded demo workspace."""
    seeded_info = seed_workspace_data(db, tenant_id=DEMO_TENANT_ID, reset=reset)
    token = create_session_token(
        user_id="demo-explorer",
        tenant_id=DEMO_TENANT_ID,
        email="guest@mandate.demo",
        full_name="Guest Login",
        workspace_name="Explore Sandbox (Seed Data)",
    )
    return {
        "token": token,
        "mode": "explore",
        "user": {
            "id": "demo-explorer",
            "email": "guest@mandate.demo",
            "full_name": "Guest Login",
            "workspace_name": "Explore Sandbox (Seed Data)",
            "tenant_id": DEMO_TENANT_ID,
        },
        "seeded": seeded_info,
    }


@router.get("/auth/me")
def get_current_workspace(request: Request):
    """Return active workspace session claims if signed in or exploring."""
    claims = verify_session_token(extract_token_from_request(request))
    if not claims:
        return {"mode": "public", "user": None}
    mode = "explore" if claims.get("tid") == DEMO_TENANT_ID else "authenticated"
    return {
        "mode": mode,
        "user": {
            "id": claims.get("uid"),
            "email": claims.get("email"),
            "full_name": claims.get("name"),
            "workspace_name": claims.get("ws"),
            "tenant_id": claims.get("tid"),
        },
    }


@router.post("/workspace/seed-mock")
def seed_current_workspace(
    payload: Optional[SeedWorkspaceRequest] = None,
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_db),
):
    """Populate the active workspace with sample mock data (user-triggered)."""
    reset = bool(payload.reset) if payload else False
    result = seed_workspace_data(db, tenant_id=tenant_id, reset=reset)
    return {"success": True, "tenant_id": tenant_id, "seeded": result}


@router.post("/workspace/clear")
def clear_current_workspace(
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_db),
):
    """Clear all records in the active personal or demo workspace."""
    if not (tenant_id.startswith("ws_") or tenant_id == DEMO_TENANT_ID):
        raise HTTPException(status_code=403, detail="Cannot clear the default system tenant from workspace API.")
    clear_workspace_data(db, tenant_id=tenant_id)
    return {"success": True, "tenant_id": tenant_id}

