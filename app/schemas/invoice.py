from datetime import datetime, date
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field


class LineItemSchema(BaseModel):
    name: str = Field(..., description="Item or service name")
    description: Optional[str] = None
    quantity: float = Field(1.0, gt=0)
    unit_amount: float = Field(..., ge=0)
    unit_of_measure: Optional[str] = "QUANTITY"
    tax_percent: Optional[float] = None


class InvoiceCreateRequest(BaseModel):
    client_id: str
    line_items: List[LineItemSchema]
    currency: str = "USD"
    terms_days: int = 15
    memo: Optional[str] = None
    due_date: Optional[datetime] = None
    discount_percent: Optional[float] = None
    source_input_id: Optional[str] = None


class InvoiceResponse(BaseModel):
    id: str
    tenant_id: str
    client_id: str
    paypal_invoice_id: Optional[str] = None
    paypal_invoice_number: Optional[str] = None
    status: str
    currency: str
    subtotal: float
    discount_total: float
    total: float
    amount_paid: float
    amount_due: float
    issue_date: datetime
    due_date: datetime
    terms_days: int
    line_items: List[Dict[str, Any]]
    memo: Optional[str] = None
    payer_view_url: Optional[str] = None
    last_reminder_at: Optional[datetime] = None
    reminder_count: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class InvoiceListResponse(BaseModel):
    items: List[InvoiceResponse]
    total: int
    cursor: Optional[str] = None


class ReminderSendRequest(BaseModel):
    subject: Optional[str] = "Invoice Reminder"
    note: Optional[str] = "Kindly check the outstanding invoice."


class ExternalPaymentRequest(BaseModel):
    amount: float
    method: str = "EXTERNAL"
    paid_at: Optional[datetime] = None
    note: Optional[str] = None
