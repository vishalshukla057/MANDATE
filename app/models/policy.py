from sqlalchemy import Column, String, Integer, JSON
from app.db import Base
from app.models.base import BaseModelMixin


class Policy(Base, BaseModelMixin):
    __tablename__ = "policy"

    key = Column(String(128), nullable=False, index=True)
    value = Column(JSON, nullable=False)
    version = Column(Integer, default=1, nullable=False)

