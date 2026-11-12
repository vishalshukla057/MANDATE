import uuid
from sqlalchemy import Column, String, DateTime
from app.db import Base
from app.services.clock import ClockService


class WorkspaceUser(Base):
    __tablename__ = "workspace_users"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    email = Column(String(255), nullable=False, unique=True, index=True)
    full_name = Column(String(255), nullable=False)
    workspace_name = Column(String(255), nullable=False)
    tenant_id = Column(String(64), nullable=False, unique=True, index=True)
    password_hash = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), default=ClockService.now, nullable=False)

