from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, EmailStr, Field


class ClientCreateRequest(BaseModel):
    name: str = Field(..., min_length=1)
    email: Optional[str] = None
    company: Optional[str] = None
    default_terms_days: int = 15
    currency: str = "USD"
    notes: Optional[str] = None


class ClientUpdateRequest(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    company: Optional[str] = None
    default_terms_days: Optional[int] = None
    currency: Optional[str] = None
    notes: Optional[str] = None
    risk_score: Optional[float] = None
    risk_label: Optional[str] = None


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
