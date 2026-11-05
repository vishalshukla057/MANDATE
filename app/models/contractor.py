from sqlalchemy import Column, String, Float, Text, Boolean, JSON, ForeignKey
from app.db import Base
from app.models.base import BaseModelMixin


class Contractor(Base, BaseModelMixin):
    __tablename__ = "contractors"

    name = Column(String(255), nullable=False)
    email_or_paypal_id = Column(String(255), nullable=False, index=True)
    default_split_pct = Column(Float, default=0.0, nullable=False)
    notes = Column(Text, nullable=True)
    active = Column(Boolean, default=True, nullable=False)


class PayoutRule(Base, BaseModelMixin):
    __tablename__ = "payout_rules"

    contractor_id = Column(String(36), ForeignKey("contractors.id"), nullable=False, index=True)
    trigger = Column(String(64), default="on_invoice_paid", nullable=False)  # on_invoice_paid | manual
    basis = Column(String(64), default="percent_of_invoice", nullable=False)  # percent_of_invoice | fixed
    value = Column(Float, nullable=False)
    invoice_filter = Column(JSON, default=dict, nullable=False)  # client_id, tag, etc.

