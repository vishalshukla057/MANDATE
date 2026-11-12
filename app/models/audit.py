from sqlalchemy import Column, String, Integer, JSON, UniqueConstraint
from app.db import Base
from app.models.base import BaseModelMixin


class AuditLog(Base, BaseModelMixin):
    __tablename__ = "audit_log"
    __table_args__ = (UniqueConstraint("tenant_id", "seq", name="uq_audit_tenant_seq"),)

    seq = Column(Integer, primary_key=False, autoincrement=False, index=True)
    ts = Column(String(36), nullable=False, index=True)
    actor = Column(String(32), nullable=False)  # agent | user | system | paypal
    event = Column(String(128), nullable=False, index=True)
    entity_type = Column(String(64), nullable=False)
    entity_id = Column(String(64), nullable=True, index=True)
    payload = Column(JSON, default=dict, nullable=False)
    prev_hash = Column(String(64), nullable=False)
    hash = Column(String(64), nullable=False, index=True)

    @property
    def entry_hash(self) -> str:
        return self.hash
