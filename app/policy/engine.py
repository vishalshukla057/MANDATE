import logging
from datetime import timedelta
from typing import Any, Dict, List, Optional
from sqlalchemy.orm import Session

from app.config import settings
from app.models.policy import Policy
from app.models.client import Client
from app.models.invoice import Invoice
from app.models.dispute import Dispute
from app.models.payment import Payment
from app.models.contractor import Contractor
from app.models.payout import Payout
from app.models.agent import AgentAction
from app.services.clock import ClockService
from app.policy.rules import PolicyDecision

logger = logging.getLogger("policy_engine")


class PolicyEngine:
    @classmethod
    def get_runtime_policy(cls, db: Session, key: str, default_val: Any) -> Any:
        record = db.query(Policy).filter(Policy.key == key).order_by(Policy.version.desc()).first()
        if record:
            return record.value
        return default_val

    @classmethod
    def evaluate(
        cls,
        db: Session,
        tool_name: str,
        args: Dict[str, Any],
        tenant_id: Optional[str] = None,
    ) -> PolicyDecision:
        tenant = tenant_id or settings.DEFAULT_TENANT_ID

        # 1. Kill Switch Check
        kill_switch_active = cls.get_runtime_policy(db, "KILL_SWITCH", settings.KILL_SWITCH)
        is_write_tool = tool_name not in [
            "parse_invoice_input",
            "lookup_client",
            "list_invoices",
            "get_invoice",
            "get_cash_flow_forecast",
            "get_client_payment_history",
            "draft_reminder",
            "build_dispute_evidence",
            "draft_dispute_response",
            "answer_analytics_question",
        ]

        if kill_switch_active and is_write_tool:
            return PolicyDecision(
                decision="block",
                reasons=["Kill switch is active: all automated and agent write operations are blocked."],
                risk_tier="high",
            )

        # 2. Read tools are always allowed
        if not is_write_tool:
            return PolicyDecision(decision="allow", reasons=["Read-only inspection permitted."], risk_tier="read")

        # 3. Always require approval for sensitive / irreversible operations
        if tool_name in ["record_offline_payment", "cancel_invoice", "accept_dispute_claim", "submit_dispute_response"]:
            return PolicyDecision(
                decision="require_approval",
                reasons=[f"Action '{tool_name}' requires explicit human authorization per invariant policy."],
                risk_tier="high",
            )

        # 4. Send Invoice Policy
        if tool_name == "send_invoice":
            invoice_id = args.get("invoice_id")
            invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
            if not invoice:
                # Might be PayPal invoice ID or newly created draft
                invoice = db.query(Invoice).filter(Invoice.paypal_invoice_id == invoice_id).first()

            reasons = []
            auto_threshold = float(cls.get_runtime_policy(db, "POLICY_REQUIRE_APPROVAL_ABOVE", settings.POLICY_REQUIRE_APPROVAL_ABOVE))
            
            if invoice:
                client = db.query(Client).filter(Client.id == invoice.client_id).first()
                if invoice.total > auto_threshold:
                    reasons.append(f"Invoice total ${invoice.total:.2f} exceeds automatic dispatch threshold (${auto_threshold:.2f}).")
                if client and client.paid_count == 0:
                    reasons.append(f"Client '{client.name}' has no prior completed payments (first invoice escalation).")
                if client and client.total_billed > 0 and client.paid_count > 1:
                    avg_bill = client.total_billed / max(1, client.paid_count)
                    if invoice.total >= (avg_bill * 5.0):
                        reasons.append(f"Anomaly detected: Invoice total is {invoice.total / avg_bill:.1f}x client historical average.")

                # Check duplicate invoice in last 24h
                twenty_four_hours_ago = ClockService.now() - timedelta(hours=24)
                duplicate = (
                    db.query(Invoice)
                    .filter(Invoice.client_id == invoice.client_id)
                    .filter(Invoice.id != invoice.id)
                    .filter(Invoice.total == invoice.total)
                    .filter(Invoice.created_at >= twenty_four_hours_ago)
                    .first()
                )
                if duplicate:
                    reasons.append("Anomaly detected: Duplicate invoice with identical amount issued to this client within 24 hours.")

            if reasons:
                return PolicyDecision(decision="require_approval", reasons=reasons, risk_tier="medium")
            return PolicyDecision(decision="allow", reasons=["Invoice within automatic parameters."], risk_tier="low")

        # 5. Send Reminder Policy
        if tool_name == "send_reminder":
            invoice_id = args.get("invoice_id")
            invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
            if not invoice:
                invoice = db.query(Invoice).filter(Invoice.paypal_invoice_id == invoice_id).first()

            if invoice:
                # Dispute check
                dispute = db.query(Dispute).filter(Dispute.invoice_id == invoice.id, Dispute.status != "RESOLVED").first()
                if dispute:
                    return PolicyDecision(
                        decision="block",
                        reasons=["Active dispute on invoice: reminders blocked until resolution."],
                        risk_tier="low",
                    )

                # Recent partial payment check (last 48 hours)
                two_days_ago = ClockService.now() - timedelta(hours=48)
                recent_payment = (
                    db.query(Payment)
                    .filter(Payment.invoice_id == invoice.id, Payment.paid_at >= two_days_ago)
                    .first()
                )
                if recent_payment:
                    return PolicyDecision(
                        decision="block",
                        reasons=["Partial payment received within last 48h; standard chasing reminder suppressed."],
                        risk_tier="low",
                    )

                # Reminder frequency check (max 1 per 3 days)
                if invoice.last_reminder_at:
                    days_since_last = (ClockService.now() - invoice.last_reminder_at).total_seconds() / 86400.0
                    if days_since_last < 3.0:
                        return PolicyDecision(
                            decision="block",
                            reasons=[f"Frequency limit: Last reminder was sent {days_since_last:.1f} days ago (minimum 3 days required)."],
                            risk_tier="low",
                        )

                # Max reminder count check
                if invoice.reminder_count >= 4:
                    return PolicyDecision(
                        decision="require_approval",
                        reasons=["Maximum automated reminders (4) reached. Requires human review for legal/formal escalation."],
                        risk_tier="medium",
                    )

                # Quiet hours check (21:00 - 08:00 UTC)
                now_hour = ClockService.now().hour
                if now_hour >= 21 or now_hour < 8:
                    return PolicyDecision(
                        decision="require_approval",
                        reasons=[f"Quiet hours in effect ({now_hour}:00 UTC). Automated dispatch paused."],
                        risk_tier="low",
                    )

            auto_reminders = cls.get_runtime_policy(db, "POLICY_AUTO_APPROVE_REMINDERS", settings.POLICY_AUTO_APPROVE_REMINDERS)
            if not auto_reminders:
                return PolicyDecision(decision="require_approval", reasons=["Policy mandates approval for all reminders."], risk_tier="low")

            return PolicyDecision(decision="allow", reasons=["Reminder meets all schedule and frequency policies."], risk_tier="low")

        # 6. Discount Proposal Policy
        if tool_name == "propose_early_pay_discount":
            discount_pct = float(args.get("discount_percent", 0.0))
            max_discount = float(cls.get_runtime_policy(db, "POLICY_MAX_DISCOUNT_PCT", settings.POLICY_MAX_DISCOUNT_PCT))
            if discount_pct > max_discount:
                return PolicyDecision(
                    decision="block",
                    reasons=[f"Requested discount of {discount_pct:.1f}% exceeds hard cap of {max_discount:.1f}%."],
                    risk_tier="medium",
                )
            return PolicyDecision(decision="allow", reasons=[f"Discount of {discount_pct:.1f}% within permitted policy."], risk_tier="medium")

        # 7. Contractor Payouts
        if tool_name in ["propose_payout", "execute_payout"]:
            amount = float(args.get("amount", 0.0))
            contractor_id = args.get("contractor_id")
            invoice_id = args.get("invoice_id")

            contractor = db.query(Contractor).filter(Contractor.id == contractor_id).first()
            if not contractor or not contractor.active:
                return PolicyDecision(decision="block", reasons=["Target contractor is not in approved allowlist or is inactive."], risk_tier="high")

            # Check invoice received funds
            if invoice_id:
                inv = db.query(Invoice).filter(Invoice.id == invoice_id).first()
                if inv and amount > inv.amount_paid:
                    return PolicyDecision(
                        decision="block",
                        reasons=[f"Solvency violation: Payout ${amount:.2f} exceeds received invoice funds (${inv.amount_paid:.2f})."],
                        risk_tier="high",
                    )

            # Daily cap check
            start_of_day = ClockService.now().replace(hour=0, minute=0, second=0, microsecond=0)
            daily_total = sum(
                p.amount for p in db.query(Payout).filter(Payout.created_at >= start_of_day, Payout.status.in_(["approved", "submitted", "succeeded"])).all()
            )
            daily_cap = float(cls.get_runtime_policy(db, "POLICY_MAX_PAYOUT_PER_DAY", settings.POLICY_MAX_PAYOUT_PER_DAY))
            if (daily_total + amount) > daily_cap:
                return PolicyDecision(
                    decision="block",
                    reasons=[f"Daily payout limit breach: Total daily payouts (${daily_total + amount:.2f}) exceed cap (${daily_cap:.2f})."],
                    risk_tier="high",
                )

            max_single = float(cls.get_runtime_policy(db, "POLICY_MAX_PAYOUT_PER_TX", settings.POLICY_MAX_PAYOUT_PER_TX))
            if amount > max_single:
                return PolicyDecision(
                    decision="require_approval",
                    reasons=[f"Payout amount ${amount:.2f} exceeds per-transaction limit (${max_single:.2f})."],
                    risk_tier="high",
                )

            # Check if first payout to this contractor
            prior_payouts = db.query(Payout).filter(Payout.contractor_id == contractor_id, Payout.status == "succeeded").count()
            if prior_payouts == 0:
                return PolicyDecision(
                    decision="require_approval",
                    reasons=[f"First payout to contractor '{contractor.name}': initial compliance authorization required."],
                    risk_tier="high",
                )

            return PolicyDecision(decision="allow", reasons=["Payout satisfies all automated safety thresholds."], risk_tier="high")

        # Default fallback
        return PolicyDecision(decision="allow", reasons=["Standard action allowed."], risk_tier="low")

