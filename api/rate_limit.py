from __future__ import annotations

import math
import os
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from threading import Lock

from fastapi import HTTPException


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    limit: int
    window_seconds: int
    remaining: int
    retry_after_seconds: int = 0


_buckets: dict[str, deque[float]] = defaultdict(deque)
_lock = Lock()


def rate_limit_enabled() -> bool:
    return os.getenv("AUTOOPS_RATE_LIMIT_ENABLED", "true").lower() == "true"


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def rate_limit_config() -> dict:
    return {
        "enabled": rate_limit_enabled(),
        "global_requests_per_minute": _env_int("AUTOOPS_RATE_LIMIT_REQUESTS_PER_MINUTE", 600),
        "job_submits_per_minute": _env_int("AUTOOPS_RATE_LIMIT_JOB_SUBMITS_PER_MINUTE", 20),
        "window_seconds": _env_int("AUTOOPS_RATE_LIMIT_WINDOW_SECONDS", 60),
    }


def reset_rate_limits() -> None:
    with _lock:
        _buckets.clear()


def check_rate_limit(
    scope: str,
    key: str,
    *,
    limit: int,
    window_seconds: int,
    now: float | None = None,
) -> RateLimitResult:
    if limit <= 0 or window_seconds <= 0:
        return RateLimitResult(True, limit=max(1, limit), window_seconds=max(1, window_seconds), remaining=limit)

    current_time = time.monotonic() if now is None else now
    bucket_key = f"{scope}:{key}"
    with _lock:
        bucket = _buckets[bucket_key]
        while bucket and current_time - bucket[0] >= window_seconds:
            bucket.popleft()

        if len(bucket) >= limit:
            retry_after = max(1, math.ceil(window_seconds - (current_time - bucket[0])))
            return RateLimitResult(
                allowed=False,
                limit=limit,
                window_seconds=window_seconds,
                remaining=0,
                retry_after_seconds=retry_after,
            )

        bucket.append(current_time)
        return RateLimitResult(
            allowed=True,
            limit=limit,
            window_seconds=window_seconds,
            remaining=max(0, limit - len(bucket)),
        )


def headers_for(result: RateLimitResult) -> dict[str, str]:
    headers = {
        "X-RateLimit-Limit": str(result.limit),
        "X-RateLimit-Remaining": str(result.remaining),
        "X-RateLimit-Window": str(result.window_seconds),
    }
    if not result.allowed:
        headers["Retry-After"] = str(result.retry_after_seconds)
    return headers


def check_global_rate_limit(client_key: str) -> RateLimitResult:
    config = rate_limit_config()
    if not config["enabled"]:
        return RateLimitResult(True, config["global_requests_per_minute"], config["window_seconds"], config["global_requests_per_minute"])
    return check_rate_limit(
        "global",
        client_key,
        limit=config["global_requests_per_minute"],
        window_seconds=config["window_seconds"],
    )


def enforce_job_submit_rate_limit(actor: str) -> None:
    config = rate_limit_config()
    if not config["enabled"]:
        return
    result = check_rate_limit(
        "job_submit",
        actor,
        limit=config["job_submits_per_minute"],
        window_seconds=config["window_seconds"],
    )
    if not result.allowed:
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded for investigation submissions.",
            headers=headers_for(result),
        )
