from sqlalchemy import Column, String, Integer, Float, Text, JSON, DateTime, ForeignKey
from app.db import Base
from app.models.base import BaseModelMixin


class Invoice(Base, BaseModelMixin):
    __tablename__ = "invoices"

    client_id = Column(String(36), ForeignKey("clients.id"), nullable=False, index=True)
    paypal_invoice_id = Column(String(64), nullable=True, index=True)
    paypal_invoice_number = Column(String(64), nullable=True, index=True)
    
    # draft | sent | viewed | partially_paid | paid | overdue | cancelled | refunded | disputed
    status = Column(String(32), default="draft", nullable=False, index=True)
    currency = Column(String(10), default="USD", nullable=False)
    
    subtotal = Column(Float, default=0.0, nullable=False)
    discount_total = Column(Float, default=0.0, nullable=False)
    total = Column(Float, default=0.0, nullable=False)
    amount_paid = Column(Float, default=0.0, nullable=False)
    amount_due = Column(Float, default=0.0, nullable=False)
    
    issue_date = Column(DateTime(timezone=True), nullable=False)
    due_date = Column(DateTime(timezone=True), nullable=False, index=True)
    terms_days = Column(Integer, default=15, nullable=False)
    
    line_items = Column(JSON, default=list, nullable=False)
    memo = Column(Text, nullable=True)
    payer_view_url = Column(String(512), nullable=True)
    source_input_id = Column(String(36), nullable=True)
    
    last_reminder_at = Column(DateTime(timezone=True), nullable=True)
    reminder_count = Column(Integer, default=0, nullable=False)
    
    payment_plan_id = Column(String(36), nullable=True)
    discount_offer_id = Column(String(36), nullable=True)

