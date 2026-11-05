from sqlalchemy import Column, String, Float, DateTime, ForeignKey
from app.db import Base
from app.models.base import BaseModelMixin
from app.services.clock import ClockService


class Payment(Base, BaseModelMixin):
    __tablename__ = "payments"

    invoice_id = Column(String(36), ForeignKey("invoices.id"), nullable=False, index=True)
    paypal_transaction_id = Column(String(64), nullable=True, index=True)
    amount = Column(Float, nullable=False)
    currency = Column(String(10), default="USD", nullable=False)
    method = Column(String(64), default="PAYPAL", nullable=False)
    paid_at = Column(DateTime(timezone=True), default=ClockService.now, nullable=False)
    raw_event_id = Column(String(64), nullable=True)

