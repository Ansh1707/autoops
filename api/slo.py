"""Service-level objective evaluation for local and production AutoOps."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass

from api.metrics import JobsMetrics, MetricsRegistry


@dataclass(frozen=True)
class SloObjective:
    name: str
    ok: bool
    target: str
    value: float
    detail: str


def _env_float(name: str, default: float, minimum: float = 0.0) -> float:
    try:
        return max(minimum, float(os.getenv(name, str(default))))
    except ValueError:
        return default


def _request_latency_p95_ms(snapshot: dict) -> float:
    p95_values = [
        stats.get("p95", 0.0) * 1000
        for key, stats in snapshot.get("histograms", {}).items()
        if key.startswith("autoops_api_request_duration")
    ]
    return max(p95_values) if p95_values else 0.0


def evaluate_slos(job_metrics: JobsMetrics, registry: MetricsRegistry) -> dict:
    snapshot = registry.snapshot()
    max_active_jobs = _env_float("AUTOOPS_SLO_MAX_ACTIVE_JOBS", 20)
    max_failed_ratio = _env_float("AUTOOPS_SLO_MAX_FAILED_JOB_RATIO", 0.25)
    max_p95_latency_ms = _env_float("AUTOOPS_SLO_MAX_P95_LATENCY_MS", 2000)

    failed_jobs = job_metrics.jobs_by_status.get("FAILED", 0)
    failed_ratio = failed_jobs / job_metrics.jobs_total if job_metrics.jobs_total else 0.0
    p95_latency_ms = _request_latency_p95_ms(snapshot)

    objectives = [
        SloObjective(
            name="active_job_backlog",
            ok=job_metrics.jobs_active <= max_active_jobs,
            target=f"active jobs <= {max_active_jobs:g}",
            value=float(job_metrics.jobs_active),
            detail="Non-terminal job backlog should remain bounded.",
        ),
        SloObjective(
            name="failed_job_ratio",
            ok=failed_ratio <= max_failed_ratio,
            target=f"failed job ratio <= {max_failed_ratio:g}",
            value=failed_ratio,
            detail="Failed jobs should remain a small share of total completed work.",
        ),
        SloObjective(
            name="api_request_latency_p95_ms",
            ok=p95_latency_ms <= max_p95_latency_ms,
            target=f"p95 latency <= {max_p95_latency_ms:g}ms",
            value=p95_latency_ms,
            detail="Observed in-process API latency should stay below the local SLO.",
        ),
    ]
    failed = [objective for objective in objectives if not objective.ok]
    return {
        "ok": not failed,
        "failed": len(failed),
        "objectives": [asdict(objective) for objective in objectives],
    }
