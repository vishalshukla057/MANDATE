from sqlalchemy import Column, String, Float, ForeignKey
from app.db import Base
from app.models.base import BaseModelMixin


class Payout(Base, BaseModelMixin):
    __tablename__ = "payouts"

    contractor_id = Column(String(36), ForeignKey("contractors.id"), nullable=False, index=True)
    invoice_id = Column(String(36), ForeignKey("invoices.id"), nullable=True, index=True)
    paypal_batch_id = Column(String(64), nullable=True, index=True)
    paypal_item_id = Column(String(64), nullable=True, index=True)
    amount = Column(Float, nullable=False)
    currency = Column(String(10), default="USD", nullable=False)
    
    # proposed | pending_approval | approved | submitted | succeeded | failed | rejected
    status = Column(String(32), default="proposed", nullable=False, index=True)
    action_id = Column(String(36), nullable=True)

