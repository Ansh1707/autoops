from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from api.models import AuditEvent


REDACTED = "[REDACTED]"
SENSITIVE_KEYS = {
    "access_token",
    "authorization",
    "credentials",
    "gmail_token",
    "jwt",
    "password",
    "secret",
    "token",
}


def actor_from_claims(claims: dict | None) -> str:
    if not claims:
        return "anonymous"
    return str(claims.get("sub") or claims.get("username") or "authenticated")


def sanitize_metadata(value: Any) -> Any:
    if isinstance(value, dict):
        clean = {}
        for key, item in value.items():
            if any(sensitive in str(key).lower() for sensitive in SENSITIVE_KEYS):
                clean[str(key)] = REDACTED
            else:
                clean[str(key)] = sanitize_metadata(item)
        return clean
    if isinstance(value, list):
        return [sanitize_metadata(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_metadata(item) for item in value]
    return value


def canonical_event_payload(
    *,
    actor: str,
    action: str,
    resource_type: str,
    resource_id: str | None,
    request_id: str | None,
    metadata: dict,
    previous_hash: str | None,
    created_at: datetime,
) -> str:
    return json.dumps(
        {
            "actor": actor,
            "action": action,
            "created_at": created_at.isoformat(),
            "metadata_json": metadata,
            "previous_hash": previous_hash,
            "request_id": request_id,
            "resource_id": resource_id,
            "resource_type": resource_type,
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def event_hash_for(**kwargs) -> str:
    return hashlib.sha256(canonical_event_payload(**kwargs).encode("utf-8")).hexdigest()


def latest_event_hash(db: Session) -> str | None:
    latest = db.query(AuditEvent).order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc()).first()
    return latest.event_hash if latest else None


def record_audit_event(
    db: Session,
    *,
    actor: str,
    action: str,
    resource_type: str,
    resource_id: str | None = None,
    request_id: str | None = None,
    metadata: dict | None = None,
) -> AuditEvent:
    created_at = datetime.utcnow()
    safe_metadata = sanitize_metadata(metadata or {})
    previous_hash = latest_event_hash(db)
    digest = event_hash_for(
        actor=actor,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        request_id=request_id,
        metadata=safe_metadata,
        previous_hash=previous_hash,
        created_at=created_at,
    )
    event = AuditEvent(
        actor=actor,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        request_id=request_id,
        metadata_json=safe_metadata,
        previous_hash=previous_hash,
        event_hash=digest,
        created_at=created_at,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


def list_audit_events(db: Session, limit: int = 50) -> list[AuditEvent]:
    safe_limit = max(1, min(limit, 200))
    return db.query(AuditEvent).order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc()).limit(safe_limit).all()


def verify_audit_chain(db: Session) -> dict:
    events = db.query(AuditEvent).order_by(AuditEvent.created_at.asc(), AuditEvent.id.asc()).all()
    previous_hash = None
    for index, event in enumerate(events):
        expected = event_hash_for(
            actor=event.actor,
            action=event.action,
            resource_type=event.resource_type,
            resource_id=event.resource_id,
            request_id=event.request_id,
            metadata=event.metadata_json or {},
            previous_hash=previous_hash,
            created_at=event.created_at,
        )
        if event.previous_hash != previous_hash:
            return {
                "ok": False,
                "events_checked": index,
                "failure": "previous_hash_mismatch",
                "event_id": event.id,
            }
        if event.event_hash != expected:
            return {
                "ok": False,
                "events_checked": index,
                "failure": "event_hash_mismatch",
                "event_id": event.id,
            }
        previous_hash = event.event_hash
    return {
        "ok": True,
        "events_checked": len(events),
        "latest_hash": previous_hash,
    }
