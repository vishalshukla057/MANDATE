import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime
from app.db import Base
from app.services.clock import ClockService
from app.config import settings


class BaseModelMixin:
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String(64), nullable=False, default=lambda: settings.DEFAULT_TENANT_ID, index=True)
    created_at = Column(DateTime(timezone=True), default=ClockService.now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=ClockService.now, onupdate=ClockService.now, nullable=False)

