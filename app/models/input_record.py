from sqlalchemy import Column, String, Float, Text, JSON
from app.db import Base
from app.models.base import BaseModelMixin


class InputRecord(Base, BaseModelMixin):
    __tablename__ = "inputs"

    kind = Column(String(32), nullable=False)  # text | email | voice
    raw_content = Column(Text, nullable=False)
    parsed_json = Column(JSON, nullable=True)
    parse_confidence = Column(Float, default=0.0, nullable=False)
    status = Column(String(32), default="pending", nullable=False)  # pending | parsed | failed | confirmed
    created_invoice_id = Column(String(36), nullable=True)

