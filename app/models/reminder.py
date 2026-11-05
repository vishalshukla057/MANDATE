from sqlalchemy import Column, String, Text, DateTime, ForeignKey
from app.db import Base
from app.models.base import BaseModelMixin


class Reminder(Base, BaseModelMixin):
    __tablename__ = "reminders"

    invoice_id = Column(String(36), ForeignKey("invoices.id"), nullable=False, index=True)
    scheduled_for = Column(DateTime(timezone=True), nullable=True)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    channel = Column(String(32), default="paypal_email", nullable=False)
    
    # friendly | neutral | firm | final
    tone = Column(String(32), default="friendly", nullable=False)
    subject = Column(String(255), nullable=False)
    body = Column(Text, nullable=False)
    reasoning = Column(Text, nullable=False)  # human-readable explanation citing signals
    
    # proposed | approved | sent | skipped
    status = Column(String(32), default="proposed", nullable=False, index=True)
    action_id = Column(String(36), nullable=True)

