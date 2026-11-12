import re
from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator

_CLIENT_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _normalize_client_email(v: Optional[str]) -> Optional[str]:
    if v is None:
        return None
    cleaned = v.strip()
    if not cleaned:
        return None
    if not _CLIENT_EMAIL_RE.match(cleaned):
        raise ValueError("Invalid email address. Please enter a full email (e.g. name@company.com) or leave blank.")
    return cleaned


class ClientCreateRequest(BaseModel):
    name: str = Field(..., min_length=1)
    email: Optional[str] = None
    company: Optional[str] = None
    default_terms_days: int = 15
    currency: str = "USD"
    notes: Optional[str] = None

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: Optional[str]) -> Optional[str]:
        return _normalize_client_email(v)


class ClientUpdateRequest(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    company: Optional[str] = None
    default_terms_days: Optional[int] = None
    currency: Optional[str] = None
    notes: Optional[str] = None
    risk_score: Optional[float] = None
    risk_label: Optional[str] = None

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: Optional[str]) -> Optional[str]:
        return _normalize_client_email(v)


class ClientResponse(BaseModel):
    id: str
    tenant_id: str
    name: str
    email: Optional[str] = None
    company: Optional[str] = None
    default_terms_days: int
    currency: str
    notes: Optional[str] = None
    risk_score: float
    risk_label: str
    avg_days_to_pay: float
    late_count: int
    paid_count: int
    total_billed: float
    total_paid: float
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ClientRiskProfileResponse(BaseModel):
    client_id: str
    name: str
    risk_score: float
    risk_label: str
    avg_days_to_pay: float
    late_count: int
    paid_count: int
    factors: List[str]
    signals: Dict[str, Any]
