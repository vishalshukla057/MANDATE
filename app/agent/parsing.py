import json
import re
from typing import Any, Dict, List, Optional
import httpx
from pydantic import BaseModel, Field, computed_field
from sqlalchemy.orm import Session
from app.models.client import Client
from app.config import settings

try:
    import anthropic
except ImportError:
    anthropic = None


class ParsedLineItem(BaseModel):
    description: str
    quantity: float = 1.0
    unit: str = "item"
    unit_price: float


class ParsedClientInfo(BaseModel):
    name: str
    email: Optional[str] = None
    company: Optional[str] = None


class ParsedInvoiceDraft(BaseModel):
    client: ParsedClientInfo
    line_items: List[ParsedLineItem]
    currency: str = "USD"
    terms_days: int = 15
    tax_percent: Optional[float] = None
    discount: Optional[float] = None
    memo: Optional[str] = None
    client_flags: List[str] = Field(default_factory=list)
    confidence: float = 0.9
    missing_fields: List[str] = Field(default_factory=list)
    assumptions: List[str] = Field(default_factory=list)

    @computed_field
    @property
    def total(self) -> float:
        return sum(float(item.quantity) * float(item.unit_price) for item in self.line_items)


SYSTEM_PARSER_PROMPT = """
You are an expert financial parsing assistant. Extract invoice details from messy, unformatted text, emails, or transcripts.
Return ONLY valid JSON matching this exact schema:
{
  "client": {"name": string, "email": string or null, "company": string or null},
  "line_items": [{"description": string, "quantity": number, "unit": string, "unit_price": number}],
  "currency": "USD",
  "terms_days": integer (e.g. 15, 30),
  "tax_percent": number or null,
  "discount": number or null,
  "memo": string or null,
  "client_flags": [string],
  "confidence": float (0.0 to 1.0),
  "missing_fields": [string],
  "assumptions": [string]
}

CRITICAL RULES:
1. NEVER invent email addresses or prices. If missing, leave null and list in missing_fields.
2. If the user mentions "half upfront" or "50% upfront", set the unit price or quantity to represent 50% and state this in assumptions.
3. Clean forwarded email artifacts (e.g., '> From:', 'Subject:', signatures, email disclaimers).
4. Parse relative payment terms: "net 15" -> 15, "net 30" -> 30, "due on receipt" -> 0, "net 45" -> 45.
"""


def fuzzy_match_client(db: Session, raw_name: str) -> Optional[Client]:
    """Finds existing client by exact or case-insensitive partial name."""
    if not raw_name:
        return None
    raw_clean = raw_name.strip().lower()
    clients = db.query(Client).all()
    for c in clients:
        if c.name.lower() == raw_clean or raw_clean in c.name.lower() or c.name.lower() in raw_clean:
            return c
    return None


def parse_messy_input_heuristically(raw_text: str) -> ParsedInvoiceDraft:
    """
    Deterministic rule-based extractor for offline execution, testing, and fallback.
    Accurately extracts client name, hours/quantity, rates, net terms, and missing fields.
    """
    text = raw_text.strip()
    assumptions = []
    missing_fields = []
    client_flags = []

    # 1. Terms days
    terms_match = re.search(r"net\s*(\d+)", text, re.IGNORECASE)
    if terms_match:
        terms_days = int(terms_match.group(1))
    elif re.search(r"due on receipt|immediately", text, re.IGNORECASE):
        terms_days = 0
    elif re.search(r"end of month", text, re.IGNORECASE):
        terms_days = 30
        assumptions.append("End of month mapped to 30 days")
    else:
        terms_days = 15
        assumptions.append("Default terms days 15 applied")

    # 2. Email detection
    email_match = re.search(r"[\w\.-]+@[\w\.-]+\.\w+", text)
    client_email = email_match.group(0) if email_match else None
    if not client_email:
        missing_fields.append("client_email")

    # 3. Client Name detection
    # Common patterns: "Invoice [Client]", "Bill [Client]", "Send invoice to [Client]"
    name_match = re.search(
        r"(?:invoice|bill|charge|send invoice to|client:?)\s+([A-Z][a-zA-Z0-9_\s&]+?)(?:,|\s+for|\s+\d|\s+at|\s+net|$)",
        text,
        re.IGNORECASE,
    )
    if name_match:
        client_name = name_match.group(1).strip()
    else:
        first_line = text.split("\n")[0]
        client_name = first_line[:30].strip() or "Client"

    # 4. Hours / Quantity and Rate detection
    # Patterns: "40 hours at $85", "40 hrs @ $85/hr", "10 units at $150", "$5,000 for website"
    qty = 1.0
    rate = 0.0
    unit = "item"
    desc = "Professional Services"

    hours_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:hours|hrs|hr)", text, re.IGNORECASE)
    rate_match = re.search(r"(?:at|@|\$)\s*(\$?\d+(?:,\d+)?(?:\.\d+)?)(?:\s*/\s*(?:hr|hour|unit))?", text, re.IGNORECASE)

    if hours_match:
        qty = float(hours_match.group(1))
        unit = "hours"

    if rate_match:
        rate_str = rate_match.group(1).replace("$", "").replace(",", "")
        rate = float(rate_str)
    else:
        # Fallback to standalone dollar amount
        dollar_match = re.search(r"\$\s*(\d+(?:,\d+)?(?:\.\d+)?)", text)
        if dollar_match:
            rate = float(dollar_match.group(1).replace(",", ""))

    if rate == 0.0:
        missing_fields.append("amount")

    # 5. Half upfront detection
    if re.search(r"half upfront|50%\s*upfront", text, re.IGNORECASE):
        qty = qty * 0.5
        assumptions.append("Created first invoice for 50% per 'half upfront' instruction")

    confidence = 0.95 if not missing_fields else 0.70

    line_item = ParsedLineItem(
        description=desc,
        quantity=qty,
        unit=unit,
        unit_price=rate,
    )

    return ParsedInvoiceDraft(
        client=ParsedClientInfo(name=client_name, email=client_email),
        line_items=[line_item],
        currency="USD",
        terms_days=terms_days,
        confidence=confidence,
        missing_fields=missing_fields,
        assumptions=assumptions,
        client_flags=client_flags,
    )


async def parse_invoice_input_string(raw_text: str, db: Optional[Session] = None) -> ParsedInvoiceDraft:
    """
    Parses messy text into structured invoice draft.
    Uses Google Gemini (or Anthropic Claude if configured); otherwise falls back to deterministic heuristic parser.
    Fuzzy-matches against existing clients in the DB when available.
    """
    parsed: Optional[ParsedInvoiceDraft] = None

    if settings.GEMINI_API_KEY and settings.GEMINI_API_KEY != "mock_gemini_key":
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{settings.GEMINI_MODEL}:generateContent"
            headers = {"Content-Type": "application/json", "x-goog-api-key": settings.GEMINI_API_KEY}
            body = {
                "systemInstruction": {"parts": [{"text": SYSTEM_PARSER_PROMPT}]},
                "contents": [{"role": "user", "parts": [{"text": raw_text}]}],
                "generationConfig": {"responseMimeType": "application/json"},
            }
            async with httpx.AsyncClient(timeout=20.0) as client:
                res = await client.post(url, headers=headers, json=body)
                if res.status_code < 400:
                    candidates = res.json().get("candidates") or []
                    parts = candidates[0].get("content", {}).get("parts", []) if candidates else []
                    content_text = "".join(p.get("text", "") for p in parts)
                    json_match = re.search(r"\{.*\}", content_text, re.DOTALL)
                    if json_match:
                        parsed = ParsedInvoiceDraft(**json.loads(json_match.group(0)))
        except Exception:
            parsed = None

    if not parsed and settings.ANTHROPIC_API_KEY and settings.ANTHROPIC_API_KEY != "mock_anthropic_key" and anthropic is not None:
        try:
            client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
            response = await client.messages.create(
                model=settings.LLM_MODEL,
                max_tokens=1000,
                system=SYSTEM_PARSER_PROMPT,
                messages=[{"role": "user", "content": raw_text}],
            )
            content_text = response.content[0].text
            json_match = re.search(r"\{.*\}", content_text, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group(0))
                parsed = ParsedInvoiceDraft(**data)
        except Exception:
            parsed = None

    if not parsed:
        parsed = parse_messy_input_heuristically(raw_text)

    # Reconcile client with existing DB records
    if db and parsed.client.name:
        existing_client = fuzzy_match_client(db, parsed.client.name)
        if existing_client:
            parsed.client.name = existing_client.name
            if not parsed.client.email and existing_client.email:
                parsed.client.email = existing_client.email
                if "client_email" in parsed.missing_fields:
                    parsed.missing_fields.remove("client_email")
            parsed.assumptions.append(f"Matched existing client '{existing_client.name}' (ID: {existing_client.id})")

    return parsed

