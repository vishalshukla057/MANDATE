import logging
from typing import Any, Dict, List, Optional
from sqlalchemy.orm import Session

from app.config import settings
from app.models.contractor import Contractor, PayoutRule
from app.models.payout import Payout
from app.models.invoice import Invoice
from app.models.agent import AgentAction
from app.services.clock import ClockService
from app.services.audit import AuditService
from app.services.feed import feed_broker
from app.policy.engine import PolicyEngine
from app.paypal.client import get_paypal_gateway, PayPalError

logger = logging.getLogger("payout_service")


class PayoutService:
    @classmethod
    async def process_invoice_paid_payouts(cls, db: Session, invoice: Invoice) -> List[Payout]:
        """
        Evaluates active payout rules upon invoice settlement and either auto-executes
        or queues payouts for human approval based on PolicyEngine rules.
        """
        rules = db.query(PayoutRule).filter(PayoutRule.trigger == "on_invoice_paid").all()
        created_payouts = []

        for rule in rules:
            contractor = db.query(Contractor).filter(Contractor.id == rule.contractor_id, Contractor.active == True).first()
            if not contractor:
                continue

            # Compute amount
            if rule.basis == "percent_of_invoice":
                amount = round(invoice.amount_paid * (rule.value / 100.0), 2)
            else:
                amount = round(rule.value, 2)

            if amount <= 0.0:
                continue

            # Check solvency
            if amount > invoice.amount_paid:
                logger.warning(f"Payout amount {amount} exceeds invoice received funds {invoice.amount_paid}")
                continue

            args = {
                "contractor_id": contractor.id,
                "invoice_id": invoice.id,
                "amount": amount,
                "currency": invoice.currency,
                "receiver": contractor.email_or_paypal_id,
            }

            policy_decision = PolicyEngine.evaluate(db, "propose_payout", args, tenant_id=invoice.tenant_id)

            payout = Payout(
                tenant_id=invoice.tenant_id,
                contractor_id=contractor.id,
                invoice_id=invoice.id,
                amount=amount,
                currency=invoice.currency,
                status="pending_approval" if policy_decision.requires_approval() else "approved",
            )
            db.add(payout)
            db.commit()
            db.refresh(payout)

            if policy_decision.requires_approval():
                # Add to agent_actions approval queue
                action = AgentAction(
                    tenant_id=invoice.tenant_id,
                    kind="contractor_payout",
                    tool_name="execute_payout",
                    args={"payout_id": payout.id, **args},
                    risk_tier="high",
                    status="pending_approval",
                    reasoning=f"Automatic split payout of ${amount:.2f} ({rule.value}% of Invoice #{invoice.paypal_invoice_number}) to {contractor.name}.",
                    signals={"invoice_total": invoice.total, "amount_paid": invoice.amount_paid, "rule_basis": rule.basis},
                    policy_decision=policy_decision.to_dict(),
                )
                db.add(action)
                payout.action_id = action.id
                db.commit()

                AuditService.record(
                    db=db,
                    actor="agent",
                    event="payout_queued_for_approval",
                    entity_type="payout",
                    entity_id=payout.id,
                    payload={"amount": amount, "contractor_name": contractor.name, "reasons": policy_decision.reasons},
                )
            else:
                # Auto-submit permitted by policy
                await cls.execute_payout_submission(db, payout)

            created_payouts.append(payout)

        return created_payouts

    @classmethod
    async def execute_payout_submission(cls, db: Session, payout: Payout) -> Dict[str, Any]:
        """Submits the payout to PayPal Payouts API."""
        contractor = db.query(Contractor).filter(Contractor.id == payout.contractor_id).first()
        gateway = get_paypal_gateway()

        batch_payload = {
            "sender_batch_header": {
                "sender_batch_id": f"PBATCH-{payout.id[:12]}",
                "email_subject": "You have received a contractor payment from Collections Copilot",
                "email_message": "Payment for completed milestones.",
            },
            "items": [
                {
                    "recipient_type": "EMAIL",
                    "amount": {"value": f"{payout.amount:.2f}", "currency": payout.currency},
                    "note": f"Invoice disbursement: #{payout.invoice_id or 'GENERAL'}",
                    "sender_item_id": f"ITEM-{payout.id[:12]}",
                    "receiver": contractor.email_or_paypal_id,
                }
            ],
        }

        try:
            res = await gateway.create_payout_batch(batch_payload, sender_batch_id=f"BATCH-{payout.id[:8]}")
            payout.paypal_batch_id = res.get("batch_header", {}).get("payout_batch_id", f"PB-{payout.id[:8]}")
            payout.status = "submitted"
            db.commit()

            AuditService.record(
                db=db,
                actor="user",
                event="payout_submitted_to_paypal",
                entity_type="payout",
                entity_id=payout.id,
                payload={"batch_id": payout.paypal_batch_id, "amount": payout.amount},
            )

            feed_broker.publish(
                "payout_submitted",
                {"payout_id": payout.id, "amount": payout.amount, "contractor": contractor.name, "batch_id": payout.paypal_batch_id},
            )
            return {"success": True, "payout_id": payout.id, "batch_id": payout.paypal_batch_id, "status": "submitted"}
        except PayPalError as pe:
            payout.status = "failed"
            db.commit()
            raise pe

