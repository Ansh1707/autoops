import json
import logging
from datetime import datetime, timezone
from typing import Any


logger = logging.getLogger("autoops")

SENSITIVE_KEY_PARTS = (
    "authorization",
    "credential",
    "password",
    "secret",
    "token",
)


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def redact_value(key: str, value: Any) -> Any:
    lowered = key.lower()
    if any(part in lowered for part in SENSITIVE_KEY_PARTS):
        return "[REDACTED]"
    return value


def safe_payload(value: Any, depth: int = 0) -> Any:
    """Return a JSON-safe, redacted payload for logs and traces."""
    if depth > 4:
        return str(value)[:500]
    if isinstance(value, dict):
        return {
            str(key): safe_payload(redact_value(str(key), item), depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [safe_payload(item, depth + 1) for item in value[:50]]
    if isinstance(value, tuple):
        return [safe_payload(item, depth + 1) for item in value[:50]]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def build_event(event: str, job_id: str | None = None, **fields: Any) -> dict[str, Any]:
    payload = {
        "event": event,
        "timestamp": utc_timestamp(),
        **fields,
    }
    if job_id:
        payload["job_id"] = job_id
    return safe_payload(payload)


def log_event(event: str, job_id: str | None = None, **fields: Any) -> dict[str, Any]:
    payload = build_event(event, job_id=job_id, **fields)
    logger.info(json.dumps(payload, default=str, sort_keys=True))
    return payload


def log_payload(payload: dict[str, Any]) -> None:
    logger.info(json.dumps(safe_payload(payload), default=str, sort_keys=True))
