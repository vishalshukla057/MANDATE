from sqlalchemy import Column, String, DateTime, JSON, ForeignKey
from app.db import Base
from app.models.base import BaseModelMixin


class Offer(Base, BaseModelMixin):
    __tablename__ = "offers"

    invoice_id = Column(String(36), ForeignKey("invoices.id"), nullable=False, index=True)
    # early_pay_discount | payment_plan
    kind = Column(String(64), nullable=False)
    terms = Column(JSON, default=dict, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    # active | accepted | expired | cancelled
    status = Column(String(32), default="active", nullable=False)

