from sqlalchemy import Column, String, Boolean, DateTime, JSON, Text
from app.db import Base
from app.models.base import BaseModelMixin
from app.services.clock import ClockService


class WebhookEvent(Base, BaseModelMixin):
    __tablename__ = "webhook_events"

    paypal_event_id = Column(String(128), unique=True, nullable=False, index=True)
    event_type = Column(String(128), nullable=False, index=True)
    payload = Column(JSON, nullable=False)
    verified = Column(Boolean, default=False, nullable=False)
    processed_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(32), default="received", nullable=False)  # received | processed | failed | ignored
    error = Column(Text, nullable=True)

