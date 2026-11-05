from sqlalchemy import Column, String, Integer, Float, Text, JSON, DateTime
from app.db import Base
from app.models.base import BaseModelMixin


class AgentRun(Base, BaseModelMixin):
    __tablename__ = "agent_runs"

    # user_message | webhook | schedule | simulation
    trigger = Column(String(32), default="user_message", nullable=False)
    input = Column(Text, nullable=False)
    plan = Column(Text, nullable=True)
    tool_calls_count = Column(Integer, default=0, nullable=False)
    tokens_in = Column(Integer, default=0, nullable=False)
    tokens_out = Column(Integer, default=0, nullable=False)
    latency_ms = Column(Float, default=0.0, nullable=False)
    final_message = Column(Text, nullable=True)


class AgentAction(Base, BaseModelMixin):
    __tablename__ = "agent_actions"

    kind = Column(String(64), nullable=False)  # invoice_create | invoice_send | reminder_send | payout | etc.
    tool_name = Column(String(64), nullable=False, index=True)
    args = Column(JSON, default=dict, nullable=False)
    result = Column(JSON, default=dict, nullable=True)
    
    # read | low | medium | high
    risk_tier = Column(String(16), default="low", nullable=False, index=True)
    
    # auto_executed | pending_approval | approved | rejected | failed | blocked
    status = Column(String(32), default="pending_approval", nullable=False, index=True)
    
    reasoning = Column(Text, nullable=False)  # Human-readable "why"
    signals = Column(JSON, default=dict, nullable=False)  # Evidence used by agent
    policy_decision = Column(JSON, default=dict, nullable=False)  # Allow/require_approval/block + reasons
    
    idempotency_key = Column(String(64), nullable=True, index=True)
    parent_run_id = Column(String(36), nullable=True, index=True)
    approved_by = Column(String(64), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)

