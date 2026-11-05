from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models.agent import AgentRun, AgentAction
from app.models.input_record import InputRecord
from app.agent.orchestrator import AgentOrchestrator
from app.agent.parsing import parse_invoice_input_string

router = APIRouter(prefix="/agent", tags=["Agent"])


class MessageRequest(BaseModel):
    message: str = Field(..., min_length=1)
    conversation_id: Optional[str] = None


class IngestTextRequest(BaseModel):
    text: str = Field(..., min_length=1)


class IngestEmailRequest(BaseModel):
    sender: str
    subject: str
    body: str


class IngestVoiceRequest(BaseModel):
    transcript: str = Field(..., min_length=1)


@router.post("/message")
async def send_agent_message(payload: MessageRequest, db: Session = Depends(get_db)):
    """Trigger an autonomous agent run with a user directive."""
    result = await AgentOrchestrator.run(
        db=db,
        user_message=payload.message,
        trigger="user_message",
        tenant_id=settings.DEFAULT_TENANT_ID,
    )
    return result


@router.get("/runs")
def list_agent_runs(limit: int = 50, offset: int = 0, db: Session = Depends(get_db)):
    runs = db.query(AgentRun).filter(AgentRun.tenant_id == settings.DEFAULT_TENANT_ID).order_by(AgentRun.created_at.desc()).offset(offset).limit(limit).all()
    return runs


@router.get("/runs/{id}")
def get_agent_run(id: str, db: Session = Depends(get_db)):
    run = db.query(AgentRun).filter(AgentRun.id == id, AgentRun.tenant_id == settings.DEFAULT_TENANT_ID).first()
    if not run:
        raise HTTPException(status_code=404, detail="Agent run not found")
    actions = db.query(AgentAction).filter(AgentAction.parent_run_id == id).all()
    return {
        "run": run,
        "actions": actions,
    }


@router.post("/ingest/text")
async def ingest_text(payload: IngestTextRequest, db: Session = Depends(get_db)):
    """Ingest unformatted text and extract structured draft."""
    parsed = await parse_invoice_input_string(payload.text, db)
    rec = InputRecord(
        tenant_id=settings.DEFAULT_TENANT_ID,
        kind="text",
        raw_content=payload.text,
        parsed_json=parsed.model_dump(),
        parse_confidence=parsed.confidence,
        status="parsed",
    )
    db.add(rec)
    db.commit()
    return {"input_id": rec.id, "parsed": parsed.model_dump()}


@router.post("/ingest/email")
async def ingest_email(payload: IngestEmailRequest, db: Session = Depends(get_db)):
    """Ingest email payload and extract structured draft."""
    combined = f"From: {payload.sender}\nSubject: {payload.subject}\n\n{payload.body}"
    parsed = await parse_invoice_input_string(combined, db)
    rec = InputRecord(
        tenant_id=settings.DEFAULT_TENANT_ID,
        kind="email",
        raw_content=combined,
        parsed_json=parsed.model_dump(),
        parse_confidence=parsed.confidence,
        status="parsed",
    )
    db.add(rec)
    db.commit()
    return {"input_id": rec.id, "parsed": parsed.model_dump()}


@router.post("/ingest/voice")
async def ingest_voice(payload: IngestVoiceRequest, db: Session = Depends(get_db)):
    """Ingest voice-note transcript and extract structured draft."""
    parsed = await parse_invoice_input_string(payload.transcript, db)
    rec = InputRecord(
        tenant_id=settings.DEFAULT_TENANT_ID,
        kind="voice",
        raw_content=payload.transcript,
        parsed_json=parsed.model_dump(),
        parse_confidence=parsed.confidence,
        status="parsed",
    )
    db.add(rec)
    db.commit()
    return {"input_id": rec.id, "parsed": parsed.model_dump()}
