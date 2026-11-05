import hashlib
import json
from datetime import datetime, date
from typing import Any, Dict, Optional
from sqlalchemy.orm import Session
from sqlalchemy import desc
from app.models.audit import AuditLog
from app.services.clock import ClockService
from app.config import settings

GENESIS_HASH = "0000000000000000000000000000000000000000000000000000000000000000"


def json_serial(obj: Any) -> Any:
    """JSON serializer for objects not serializable by default json code."""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    return str(obj)


def canonical_json(data: Any) -> str:
    """Deterministic, sorted, whitespace-free canonical JSON representation."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), default=json_serial)


def compute_hash(prev_hash: str, canonical_body: str) -> str:
    content = (prev_hash + canonical_body).encode("utf-8")
    return hashlib.sha256(content).hexdigest()


class AuditService:
    @staticmethod
    def get_latest_entry(db: Session, tenant_id: str) -> Optional[AuditLog]:
        return (
            db.query(AuditLog)
            .filter(AuditLog.tenant_id == tenant_id)
            .order_by(desc(AuditLog.seq))
            .first()
        )

    @classmethod
    def record(
        cls,
        db: Session,
        actor: str,
        event: str,
        entity_type: str,
        payload: Dict[str, Any],
        entity_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ) -> AuditLog:
        tenant = tenant_id or settings.DEFAULT_TENANT_ID
        last_entry = cls.get_latest_entry(db, tenant)
        
        prev_hash = last_entry.hash if last_entry else GENESIS_HASH
        next_seq = (last_entry.seq + 1) if (last_entry and last_entry.seq is not None) else 1
        ts_iso = ClockService.now().isoformat()

        # The body to hash contains all semantic fields of the audit record
        hashable_dict = {
            "seq": next_seq,
            "ts": ts_iso,
            "actor": actor,
            "event": event,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "payload": payload,
        }
        body_canonical = canonical_json(hashable_dict)
        entry_hash = compute_hash(prev_hash, body_canonical)

        audit_entry = AuditLog(
            tenant_id=tenant,
            seq=next_seq,
            ts=ts_iso,
            actor=actor,
            event=event,
            entity_type=entity_type,
            entity_id=entity_id,
            payload=payload,
            prev_hash=prev_hash,
            hash=entry_hash,
        )

        db.add(audit_entry)
        db.commit()
        db.refresh(audit_entry)
        return audit_entry

    @classmethod
    def verify_chain(cls, db: Session, tenant_id: Optional[str] = None) -> Dict[str, Any]:
        tenant = tenant_id or settings.DEFAULT_TENANT_ID
        entries = (
            db.query(AuditLog)
            .filter(AuditLog.tenant_id == tenant)
            .order_by(AuditLog.seq.asc())
            .all()
        )

        if not entries:
            return {
                "valid": True,
                "count": 0,
                "message": "Audit chain is empty. Genesis root is valid.",
                "root_hash": GENESIS_HASH,
            }

        expected_prev_hash = GENESIS_HASH
        for entry in entries:
            # Verify link to previous entry
            if entry.prev_hash != expected_prev_hash:
                return {
                    "valid": False,
                    "broken_at_seq": entry.seq,
                    "reason": f"Mismatched prev_hash at sequence {entry.seq}. Expected '{expected_prev_hash}', got '{entry.prev_hash}'.",
                }

            # Recompute hash
            hashable_dict = {
                "seq": entry.seq,
                "ts": entry.ts,
                "actor": entry.actor,
                "event": entry.event,
                "entity_type": entry.entity_type,
                "entity_id": entry.entity_id,
                "payload": entry.payload,
            }
            body_canonical = canonical_json(hashable_dict)
            recalculated_hash = compute_hash(entry.prev_hash, body_canonical)

            if recalculated_hash != entry.hash:
                return {
                    "valid": False,
                    "broken_at_seq": entry.seq,
                    "reason": f"Tampered hash at sequence {entry.seq}. Expected '{recalculated_hash}', got stored '{entry.hash}'.",
                }

            expected_prev_hash = entry.hash

        return {
            "valid": True,
            "count": len(entries),
            "message": "All cryptographic audit log hashes and parent linkages verified successfully.",
            "last_hash": entries[-1].hash,
        }
