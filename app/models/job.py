from sqlalchemy import Column, String, DateTime, JSON, Text
from app.db import Base
from app.models.base import BaseModelMixin
from app.services.clock import ClockService


class JobLog(Base, BaseModelMixin):
    __tablename__ = "jobs_log"

    job = Column(String(128), nullable=False, index=True)
    started_at = Column(DateTime(timezone=True), default=ClockService.now, nullable=False)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    result = Column(JSON, nullable=True)
    error = Column(Text, nullable=True)

