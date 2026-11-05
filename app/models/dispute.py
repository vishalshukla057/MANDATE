from sqlalchemy import Column, String, Float, Text, DateTime, JSON, ForeignKey
from app.db import Base
from app.models.base import BaseModelMixin


class Dispute(Base, BaseModelMixin):
    __tablename__ = "disputes"

    paypal_dispute_id = Column(String(64), nullable=False, unique=True, index=True)
    invoice_id = Column(String(36), ForeignKey("invoices.id"), nullable=True, index=True)
    transaction_id = Column(String(64), nullable=True, index=True)
    reason = Column(String(255), nullable=False)
    status = Column(String(64), default="OPEN", nullable=False, index=True)
    amount = Column(Float, default=0.0, nullable=False)
    due_by = Column(DateTime(timezone=True), nullable=True)
    evidence_pack = Column(JSON, default=dict, nullable=False)
    draft_response = Column(Text, nullable=True)
    outcome = Column(String(64), nullable=True)

