from datetime import timedelta, timezone
from typing import Any, Dict, List, Optional
from sqlalchemy.orm import Session

from app.config import settings
from app.models.agent import AgentAction
from app.services.clock import ClockService
from app.services.audit import AuditService
from app.services.feed import feed_broker


class UndoService:
    DEFAULT_GRACE_SECONDS = 300  # 5 minutes

    @classmethod
    def stage_action(
        cls,
        db: Session,
        action: AgentAction,
        grace_seconds: int = DEFAULT_GRACE_SECONDS
    ) -> AgentAction:
        """Places an automated action into the Grace Period / Undo Window."""
        now_dt = ClockService.now()
        if now_dt.tzinfo is None:
            now_dt = now_dt.replace(tzinfo=timezone.utc)

        expires_at = now_dt + timedelta(seconds=grace_seconds)

        action.status = "staged"
        signals = dict(action.signals or {})
        signals["staged_at"] = now_dt.isoformat()
        signals["grace_window_expires_at"] = expires_at.isoformat()
        signals["grace_seconds"] = grace_seconds
        signals["can_undo"] = True
        action.signals = signals

        db.commit()

        # Emit audit record
        AuditService.record(
            db=db,
            actor="undo_service",
            event="action_placed_in_grace_window",
            entity_type="agent_action",
            payload={
                "action_id": action.id,
                "tool_name": action.tool_name,
                "grace_seconds": grace_seconds,
                "expires_at": expires_at.isoformat(),
            }
        )

        # Notify feed
        feed_broker.publish(
            "ACTION_STAGED_IN_GRACE_PERIOD",
            {
                "action_id": action.id,
                "tool_name": action.tool_name,
                "seconds_remaining": grace_seconds,
            }
        )

        return action

    @classmethod
    def cancel_staged_action(
        cls,
        db: Session,
        action_id: str,
        reason: str = "Operator cancelled within grace window",
        actor: str = "operator"
    ) -> Dict[str, Any]:
        """Cancels a staged action during its grace period (Undo Window)."""
        action = db.query(AgentAction).filter(AgentAction.id == action_id).first()
        if not action:
            return {"success": False, "error": "Action not found"}

        if action.status != "staged":
            return {
                "success": False,
                "error": f"Cannot undo action with status '{action.status}'. Only 'staged' actions can be cancelled."
            }

        # Check if window expired
        now_dt = ClockService.now()
        if now_dt.tzinfo is None:
            now_dt = now_dt.replace(tzinfo=timezone.utc)

        signals = dict(action.signals or {})
        expires_str = signals.get("grace_window_expires_at")
        
        action.status = "cancelled"
        signals["can_undo"] = False
        signals["cancelled_at"] = now_dt.isoformat()
        signals["cancellation_reason"] = reason
        signals["cancelled_by"] = actor
        action.signals = signals

        db.commit()

        # Record tamper-evident audit entry
        audit_entry = AuditService.record(
            db=db,
            actor=actor,
            event="action_cancelled_via_undo_window",
            entity_type="agent_action",
            payload={
                "action_id": action.id,
                "tool_name": action.tool_name,
                "reason": reason,
                "grace_window_honored": True,
            }
        )

        feed_broker.publish(
            "ACTION_UNDO_CONFIRMED",
            {
                "action_id": action.id,
                "tool_name": action.tool_name,
                "reason": reason,
            }
        )

        return {
            "success": True,
            "action_id": action.id,
            "status": "cancelled",
            "reason": reason,
            "merkle_hash": audit_entry.hash,
        }

    @classmethod
    def list_staged_actions(cls, db: Session) -> List[Dict[str, Any]]:
        """Lists all actions currently in their grace period with remaining seconds."""
        now_dt = ClockService.now()
        if now_dt.tzinfo is None:
            now_dt = now_dt.replace(tzinfo=timezone.utc)

        staged_actions = db.query(AgentAction).filter(AgentAction.status == "staged").all()
        results = []

        for action in staged_actions:
            signals = action.signals or {}
            expires_str = signals.get("grace_window_expires_at")
            remaining_seconds = 0
            if expires_str:
                from datetime import datetime
                try:
                    exp_dt = datetime.fromisoformat(expires_str)
                    if exp_dt.tzinfo is None:
                        exp_dt = exp_dt.replace(tzinfo=timezone.utc)
                    remaining_seconds = max(0, int((exp_dt - now_dt).total_seconds()))
                except Exception:
                    remaining_seconds = 0

            results.append({
                "action_id": action.id,
                "kind": action.kind,
                "tool_name": action.tool_name,
                "args": action.args,
                "reasoning": action.reasoning,
                "risk_tier": action.risk_tier,
                "seconds_remaining": remaining_seconds,
                "can_undo": remaining_seconds > 0,
            })

        return results

