import hashlib
import json
import logging
import time
import uuid
from typing import Any, AsyncGenerator, Dict, List, Optional
from sqlalchemy.orm import Session

from app.config import settings
from app.models.agent import AgentAction, AgentRun
from app.models.client import Client
from app.models.invoice import Invoice
from app.services.clock import ClockService
from app.services.audit import AuditService
from app.services.feed import feed_broker
from app.policy.engine import PolicyEngine
from app.agent.tools import TOOL_REGISTRY, ToolDefinition

try:
    import anthropic
except ImportError:
    anthropic = None

logger = logging.getLogger("agent_orchestrator")


def generate_idempotency_key(run_id: str, tool_name: str, args: Dict[str, Any]) -> str:
    canonical = json.dumps(args, sort_keys=True)
    raw = f"{run_id}:{tool_name}:{canonical}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


class AgentOrchestrator:
    @classmethod
    async def run(
        cls,
        db: Session,
        user_message: str,
        trigger: str = "user_message",
        tenant_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        tenant = tenant_id or settings.DEFAULT_TENANT_ID
        start_time = time.time()
        run_id = str(uuid.uuid4())

        agent_run = AgentRun(
            id=run_id,
            tenant_id=tenant,
            trigger=trigger,
            input=user_message,
            tool_calls_count=0,
            tokens_in=0,
            tokens_out=0,
        )
        db.add(agent_run)
        db.commit()

        feed_broker.publish("agent_run_started", {"run_id": run_id, "trigger": trigger, "message": user_message})

        # Check if live Anthropic key is available
        has_anthropic = (
            anthropic is not None
            and bool(settings.ANTHROPIC_API_KEY)
            and settings.ANTHROPIC_API_KEY != "mock_anthropic_key"
        )

        if has_anthropic:
            final_message = await cls._run_anthropic_loop(db, agent_run, user_message)
        else:
            final_message = await cls._run_offline_loop(db, agent_run, user_message)

        duration_ms = (time.time() - start_time) * 1000
        agent_run.latency_ms = duration_ms
        agent_run.final_message = final_message
        db.commit()

        feed_broker.publish("agent_run_completed", {"run_id": run_id, "final_message": final_message, "duration_ms": duration_ms})
        return {
            "run_id": run_id,
            "final_message": final_message,
            "tool_calls_count": agent_run.tool_calls_count,
            "latency_ms": duration_ms,
        }

    @classmethod
    async def _execute_tool_step(
        cls,
        db: Session,
        agent_run: AgentRun,
        tool_name: str,
        args: Dict[str, Any],
        reasoning: str,
        signals: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Policy-bound tool execution step.
        Validates arguments -> checks PolicyEngine -> executes or queues for approval.
        """
        tool = TOOL_REGISTRY.get(tool_name)
        if not tool:
            return {"error": f"Tool '{tool_name}' not found"}

        idempotency_key = generate_idempotency_key(agent_run.id, tool_name, args)
        signals = signals or {}

        # Evaluate Policy Engine
        policy_decision = PolicyEngine.evaluate(db, tool_name, args, tenant_id=agent_run.tenant_id)
        action_status = "auto_executed"
        result_payload: Optional[Dict[str, Any]] = None

        if policy_decision.is_blocked():
            action_status = "blocked"
            result_payload = {"blocked": True, "reasons": policy_decision.reasons}
        elif policy_decision.requires_approval():
            action_status = "pending_approval"
            result_payload = {"status": "pending_approval", "reasons": policy_decision.reasons}
        else:
            # Action allowed: execute now
            try:
                result_payload = await tool.handler(db, args, idempotency_key=idempotency_key)
                action_status = "auto_executed"
            except Exception as exc:
                action_status = "failed"
                result_payload = {"error": str(exc)}

        action_record = AgentAction(
            tenant_id=agent_run.tenant_id,
            parent_run_id=agent_run.id,
            kind=tool_name,
            tool_name=tool_name,
            args=args,
            result=result_payload,
            risk_tier=policy_decision.risk_tier,
            status=action_status,
            reasoning=reasoning,
            signals=signals,
            policy_decision=policy_decision.to_dict(),
            idempotency_key=idempotency_key,
        )
        db.add(action_record)
        agent_run.tool_calls_count += 1
        db.commit()
        db.refresh(action_record)

        # Audit logging
        AuditService.record(
            db=db,
            actor="agent",
            event=f"tool_action_{action_status}",
            entity_type="agent_action",
            entity_id=action_record.id,
            payload={
                "tool_name": tool_name,
                "status": action_status,
                "risk_tier": policy_decision.risk_tier,
                "decision_reasons": policy_decision.reasons,
                "reasoning": reasoning,
            },
        )

        feed_broker.publish(
            "agent_action",
            {
                "action_id": action_record.id,
                "tool_name": tool_name,
                "status": action_status,
                "risk_tier": policy_decision.risk_tier,
                "reasoning": reasoning,
                "policy_reasons": policy_decision.reasons,
            },
        )

        return {
            "action_id": action_record.id,
            "status": action_status,
            "policy_decision": policy_decision.to_dict(),
            "result": result_payload,
        }

    @classmethod
    async def _run_offline_loop(cls, db: Session, agent_run: AgentRun, prompt: str) -> str:
        """
        Deterministic, offline tool-use loop.
        Allows testing full agent workflows and policy gating hermetically without Anthropic API keys.
        """
        # Step 1: Parse input
        parse_step = await cls._execute_tool_step(
            db=db,
            agent_run=agent_run,
            tool_name="parse_invoice_input",
            args={"text": prompt, "reasoning": "Parse natural language directive into structured financial entity"},
            reasoning="Extracting billing line items and client profile from prompt",
        )
        parsed_data = parse_step.get("result", {})
        client_info = parsed_data.get("client", {})
        client_name = client_info.get("name")
        line_items = parsed_data.get("line_items", [])
        terms_days = parsed_data.get("terms_days", 15)

        # Step 2: Create draft invoice
        draft_step = await cls._execute_tool_step(
            db=db,
            agent_run=agent_run,
            tool_name="create_invoice_draft",
            args={
                "client_name": client_name,
                "client_email": client_info.get("email"),
                "line_items": line_items,
                "terms_days": terms_days,
                "reasoning": f"Creating draft invoice for {client_name}",
            },
            reasoning=f"Creating structured PayPal draft for {client_name}",
        )
        draft_result = draft_step.get("result", {})
        invoice_id = draft_result.get("invoice_id")
        total = draft_result.get("total", 0.0)

        # Step 3: Propose to send invoice (pass through Policy Engine!)
        send_step = await cls._execute_tool_step(
            db=db,
            agent_run=agent_run,
            tool_name="send_invoice",
            args={"invoice_id": invoice_id, "reasoning": f"Dispatch invoice of ${total:.2f} to {client_name}"},
            reasoning=f"Requesting invoice dispatch for ${total:.2f}",
        )

        send_status = send_step.get("status")
        send_policy = send_step.get("policy_decision", {})

        if send_status == "pending_approval":
            reasons_str = "; ".join(send_policy.get("reasons", []))
            return (
                f"I've created draft invoice #{draft_result.get('paypal_invoice_number')} for {client_name} "
                f"totaling ${total:.2f}. Because this action requires approval ({reasons_str}), "
                f"it has been queued in your Approvals list."
            )
        elif send_status == "auto_executed":
            return (
                f"Invoice #{draft_result.get('paypal_invoice_number')} for {client_name} (${total:.2f}) "
                f"has been created and sent automatically via PayPal."
            )
        else:
            return f"Draft created for {client_name}, but dispatch was blocked: {'; '.join(send_policy.get('reasons', []))}"

    @classmethod
    async def _run_anthropic_loop(cls, db: Session, agent_run: AgentRun, prompt: str) -> str:
        """Anthropic Claude tool-use orchestrator loop."""
        client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        anthropic_tools = [tool.to_anthropic_tool() for tool in TOOL_REGISTRY.values()]

        messages = [{"role": "user", "content": prompt}]
        iterations = 0

        while iterations < settings.LLM_MAX_TOOL_ITERATIONS:
            iterations += 1
            response = await client.messages.create(
                model=settings.LLM_MODEL,
                max_tokens=1500,
                system="You are Collections Copilot, a policy-bound financial agent. Every action you propose is vetted by the Policy Engine.",
                tools=anthropic_tools,
                messages=messages,
            )

            # Check if model requested tool use
            tool_calls = [c for c in response.content if c.type == "tool_use"]
            if not tool_calls:
                # Finished, return final text
                text_blocks = [c.text for c in response.content if hasattr(c, "text")]
                return " ".join(text_blocks)

            # Add assistant message to history
            messages.append({"role": "assistant", "content": response.content})

            # Execute tool calls
            tool_results = []
            for tc in tool_calls:
                step_res = await cls._execute_tool_step(
                    db=db,
                    agent_run=agent_run,
                    tool_name=tc.name,
                    args=tc.input,
                    reasoning=tc.input.get("reasoning", "Agent initiated tool step"),
                )
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tc.id,
                    "content": json.dumps(step_res),
                })

            messages.append({"role": "user", "content": tool_results})

        return "Agent reached maximum tool iterations."

