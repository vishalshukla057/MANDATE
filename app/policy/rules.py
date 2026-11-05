from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Dict, List, Optional
from sqlalchemy.orm import Session

from app.config import settings
from app.models.client import Client
from app.models.invoice import Invoice
from app.models.reminder import Reminder
from app.models.dispute import Dispute
from app.models.payment import Payment
from app.models.contractor import Contractor
from app.models.payout import Payout
from app.services.clock import ClockService


@dataclass
class PolicyDecision:
    decision: str  # "allow" | "require_approval" | "block"
    reasons: List[str] = field(default_factory=list)
    risk_tier: str = "low"  # "read" | "low" | "medium" | "high"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def is_allowed(self) -> bool:
        return self.decision == "allow"

    def requires_approval(self) -> bool:
        return self.decision == "require_approval"

    def is_blocked(self) -> bool:
        return self.decision == "block"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision": self.decision,
            "reasons": self.reasons,
            "risk_tier": self.risk_tier,
            "metadata": self.metadata,
        }
