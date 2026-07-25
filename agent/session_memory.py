import json
import logging
import os
from typing import Any

import redis

from agent.observability import build_event


redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
redis_client = redis.from_url(redis_url, decode_responses=True)
logger = logging.getLogger("autoops.session_memory")

SESSION_TTL_SECONDS = int(os.getenv("SESSION_MEMORY_TTL_SECONDS", "86400"))


def _key(job_id: str) -> str:
    return f"autoops:session:{job_id}:steps"


def save_step(job_id: str | None, step: dict[str, Any]) -> None:
    """Persist one investigation step in Redis for short-term job memory."""
    if not job_id:
        return

    try:
        key = _key(job_id)
        redis_client.rpush(key, json.dumps(step, default=str))
        redis_client.expire(key, SESSION_TTL_SECONDS)
    except redis.RedisError as exc:
        logger.warning(
            json.dumps(
                build_event(
                    "session_memory_write_failed",
                    job_id=job_id,
                    error=str(exc),
                )
            )
        )


def load_steps(job_id: str | None) -> list[dict[str, Any]]:
    """Load prior short-term reasoning state for a job."""
    if not job_id:
        return []

    try:
        raw_steps = redis_client.lrange(_key(job_id), 0, -1)
    except redis.RedisError as exc:
        logger.warning(
            json.dumps(
                build_event(
                    "session_memory_read_failed",
                    job_id=job_id,
                    error=str(exc),
                )
            )
        )
        return []

    steps: list[dict[str, Any]] = []
    for raw in raw_steps:
        try:
            steps.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return steps


def session_memory_status() -> dict[str, Any]:
    """Return Redis session-memory health without raising into callers."""
    try:
        return {"ok": bool(redis_client.ping()), "backend": "redis"}
    except redis.RedisError as exc:
        return {"ok": False, "backend": "redis", "error": str(exc)}
