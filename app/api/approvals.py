from datetime import datetime
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.auth_context import get_tenant_id
from app.models.agent import AgentAction
from app.services.clock import ClockService
from app.services.audit import AuditService
from app.services.feed import feed_broker
from app.policy.engine import PolicyEngine
from app.agent.tools import TOOL_REGISTRY

router = APIRouter(prefix="/approvals", tags=["Approvals"])


class ApprovalActionResponse(BaseModel):
    id: str
    kind: str
    tool_name: str
    args: Dict[str, Any]
    risk_tier: str
    status: str
    reasoning: str
    signals: Dict[str, Any]
    policy_decision: Dict[str, Any]
    idempotency_key: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class RejectRequest(BaseModel):
    reason: str = Field(..., min_length=1)


class EditRequest(BaseModel):
    args: Dict[str, Any]


@router.get("", response_model=List[ApprovalActionResponse])
def list_approvals(
    status_filter: Optional[str] = Query("pending_approval", alias="status"),
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_db),
):
    query = db.query(AgentAction).filter(AgentAction.tenant_id == tenant_id)
    if status_filter:
        query = query.filter(AgentAction.status == status_filter)
    return query.order_by(AgentAction.created_at.desc()).all()


@router.post("/{id}/approve")
async def approve_action(id: str, tenant_id: str = Depends(get_tenant_id), db: Session = Depends(get_db)):
    action = db.query(AgentAction).filter(AgentAction.id == id, AgentAction.tenant_id == tenant_id).first()
    if not action:
        raise HTTPException(status_code=404, detail="Approval request not found")
    if action.status != "pending_approval":
        raise HTTPException(status_code=400, detail=f"Cannot approve action in status '{action.status}'")

    # Re-evaluate policy at execution time (state may have changed)
    policy_check = PolicyEngine.evaluate(db, action.tool_name, action.args, tenant_id=action.tenant_id)
    if policy_check.is_blocked():
        action.status = "blocked"
        db.commit()
        raise HTTPException(status_code=403, detail=f"Execution blocked by policy engine: {'; '.join(policy_check.reasons)}")

    # Execute the underlying tool handler
    tool = TOOL_REGISTRY.get(action.tool_name)
    if not tool:
        raise HTTPException(status_code=500, detail=f"Handler for tool '{action.tool_name}' not found")

    try:
        exec_result = await tool.handler(db, action.args, idempotency_key=action.idempotency_key, tenant_id=action.tenant_id)
        action.result = exec_result
        action.status = "approved"
        action.approved_by = "operator"
        action.approved_at = ClockService.now()
        db.commit()

        AuditService.record(
            db=db,
            actor="user",
            event="action_approved",
            entity_type="agent_action",
            entity_id=action.id,
            tenant_id=action.tenant_id,
            payload={"tool_name": action.tool_name, "result": exec_result},
        )

        feed_broker.publish("approval_executed", {"action_id": action.id, "tool_name": action.tool_name, "status": "approved"})
        return {"success": True, "action_id": action.id, "status": "approved", "result": exec_result}
    except Exception as exc:
        action.status = "failed"
        db.commit()
        raise HTTPException(status_code=500, detail=f"Execution error: {str(exc)}")


@router.post("/{id}/reject")
def reject_action(id: str, payload: RejectRequest, tenant_id: str = Depends(get_tenant_id), db: Session = Depends(get_db)):
    action = db.query(AgentAction).filter(AgentAction.id == id, AgentAction.tenant_id == tenant_id).first()
    if not action:
        raise HTTPException(status_code=404, detail="Approval request not found")
    if action.status != "pending_approval":
        raise HTTPException(status_code=400, detail=f"Cannot reject action in status '{action.status}'")

    action.status = "rejected"
    action.result = {"rejection_reason": payload.reason}
    action.approved_by = "operator"
    action.approved_at = ClockService.now()
    db.commit()

    AuditService.record(
        db=db,
        actor="user",
        event="action_rejected",
        entity_type="agent_action",
        entity_id=action.id,
        tenant_id=action.tenant_id,
        payload={"rejection_reason": payload.reason},
    )

    feed_broker.publish("approval_rejected", {"action_id": action.id, "reason": payload.reason})
    return {"success": True, "action_id": action.id, "status": "rejected"}


@router.post("/{id}/edit")
def edit_action(id: str, payload: EditRequest, tenant_id: str = Depends(get_tenant_id), db: Session = Depends(get_db)):
    action = db.query(AgentAction).filter(AgentAction.id == id, AgentAction.tenant_id == tenant_id).first()
    if not action:
        raise HTTPException(status_code=404, detail="Approval request not found")
    if action.status != "pending_approval":
        raise HTTPException(status_code=400, detail=f"Cannot edit action in status '{action.status}'")

    action.args = payload.args
    # Re-evaluate policy on the edited payload
    policy_check = PolicyEngine.evaluate(db, action.tool_name, action.args, tenant_id=action.tenant_id)
    action.policy_decision = policy_check.to_dict()
    db.commit()

    return {"success": True, "action_id": action.id, "policy_decision": action.policy_decision, "args": action.args}
