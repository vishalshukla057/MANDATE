from sqlalchemy import Column, String, Integer, Float, Text
from app.db import Base
from app.models.base import BaseModelMixin


class Client(Base, BaseModelMixin):
    __tablename__ = "clients"

    name = Column(String(255), nullable=False, index=True)
    email = Column(String(255), nullable=True, index=True)
    company = Column(String(255), nullable=True)
    default_terms_days = Column(Integer, default=15, nullable=False)
    currency = Column(String(10), default="USD", nullable=False)
    notes = Column(Text, nullable=True)
    
    # Risk scoring (0-100)
    risk_score = Column(Float, default=10.0, nullable=False)
    risk_label = Column(String(32), default="reliable", nullable=False)  # reliable | watch | risky
    
    # Historical behavioral metrics
    avg_days_to_pay = Column(Float, default=0.0, nullable=False)
    late_count = Column(Integer, default=0, nullable=False)
    paid_count = Column(Integer, default=0, nullable=False)
    total_billed = Column(Float, default=0.0, nullable=False)
    total_paid = Column(Float, default=0.0, nullable=False)

