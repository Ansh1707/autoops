"""Deterministic synthetic performance checks for critical AutoOps API paths."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from typing import Any, Callable

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("JWT_SECRET_KEY", "performance-policy-secret-with-at-least-32-bytes")
os.environ.setdefault("AUTOOPS_ENV", "test")
os.environ.setdefault("AUTOOPS_RATE_LIMIT_ENABLED", "false")
os.environ.setdefault("CHROMA_PERSIST_DIR", "/tmp/autoops-performance-policy-chroma")

from api import main as api_main  # noqa: E402
from api.auth import create_access_token, hash_password  # noqa: E402
from api.migrations import ensure_schema  # noqa: E402
from api.models import AuditEvent, Base, InvestigationJob, User  # noqa: E402
from api.rate_limit import reset_rate_limits  # noqa: E402
from api.status import JobStatus  # noqa: E402


DEFAULT_SAMPLE_COUNT = 12
DEFAULT_CONCURRENCY = 8
DEFAULT_TOTAL_CONCURRENT_REQUESTS = 32
P95_BUDGET_MS = 250.0
MAX_BUDGET_MS = 750.0
CONCURRENT_P95_BUDGET_MS = 500.0
MAX_JOB_LIST_LIMIT = 100
SEED_JOB_COUNT = 250


@dataclass(frozen=True)
class PolicyResult:
    check: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class RequestStats:
    status_codes: list[int]
    durations_ms: list[float]

    @property
    def p95_ms(self) -> float:
        return _percentile(self.durations_ms, 0.95)

    @property
    def max_ms(self) -> float:
        return max(self.durations_ms) if self.durations_ms else 0.0

    @property
    def all_success(self) -> bool:
        return all(200 <= status_code < 300 for status_code in self.status_codes)


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * percentile))))
    return ordered[index]


def _stats_detail(stats: RequestStats, budget_ms: float) -> str:
    return (
        f"p95={stats.p95_ms:.2f}ms max={stats.max_ms:.2f}ms "
        f"budget={budget_ms:.2f}ms codes={sorted(set(stats.status_codes))}"
    )


def _seed_database(Session: sessionmaker) -> str:
    statuses = [
        JobStatus.SUCCESS.value,
        JobStatus.FAILED.value,
        JobStatus.QUEUED.value,
        JobStatus.RUNNING.value,
        JobStatus.PLANNING.value,
    ]
    with Session() as db:
        db.add(User(username="admin", password_hash=hash_password("password"), role="owner", is_active=True))
        jobs = [
            InvestigationJob(
                goal=f"performance-policy-job-{index:03d}",
                status=statuses[index % len(statuses)],
                current_step=f"step-{index % 7}" if index % 3 == 0 else None,
                trace=[],
                result="done" if statuses[index % len(statuses)] == JobStatus.SUCCESS.value else None,
            )
            for index in range(SEED_JOB_COUNT)
        ]
        db.add_all(jobs)
        db.add(
            AuditEvent(
                actor="performance-policy",
                action="policy.seed",
                resource_type="job",
                event_hash="performance-policy-seed",
            )
        )
        db.commit()
        return jobs[0].id


def _build_client() -> tuple[TestClient, str, Callable[[], None]]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ensure_schema(engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    first_job_id = _seed_database(Session)

    def override_get_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    api_main.app.dependency_overrides[api_main.get_db] = override_get_db
    api_main.metrics_registry.reset()
    reset_rate_limits()
    client = TestClient(api_main.app)

    def cleanup() -> None:
        api_main.app.dependency_overrides.pop(api_main.get_db, None)
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
        reset_rate_limits()

    return client, first_job_id, cleanup


def _auth_headers() -> dict[str, str]:
    token = create_access_token(data={"sub": "admin", "username": "admin", "role": "owner"})
    return {"Authorization": f"Bearer {token}"}


def _measure(client: TestClient, method: str, path: str, *, headers: dict[str, str] | None = None) -> tuple[int, float, Any]:
    started = time.perf_counter()
    response = client.request(method, path, headers=headers)
    duration_ms = (time.perf_counter() - started) * 1000
    try:
        payload: Any = response.json()
    except Exception:
        payload = response.text
    return response.status_code, duration_ms, payload


def _run_sequential(
    client: TestClient,
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None,
    sample_count: int,
) -> tuple[RequestStats, Any]:
    status_codes: list[int] = []
    durations_ms: list[float] = []
    last_payload: Any = None
    for _index in range(sample_count):
        status_code, duration_ms, payload = _measure(client, method, path, headers=headers)
        status_codes.append(status_code)
        durations_ms.append(duration_ms)
        last_payload = payload
    return RequestStats(status_codes=status_codes, durations_ms=durations_ms), last_payload


def _run_concurrent(
    client: TestClient,
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None,
    total_requests: int,
    concurrency: int,
) -> RequestStats:
    status_codes: list[int] = []
    durations_ms: list[float] = []
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [
            executor.submit(_measure, client, method, path, headers=headers)
            for _index in range(total_requests)
        ]
        for future in as_completed(futures):
            status_code, duration_ms, _payload = future.result()
            status_codes.append(status_code)
            durations_ms.append(duration_ms)
    return RequestStats(status_codes=status_codes, durations_ms=durations_ms)


def run_performance_policy_checks(
    *,
    sample_count: int = DEFAULT_SAMPLE_COUNT,
    concurrency: int = DEFAULT_CONCURRENCY,
    total_concurrent_requests: int = DEFAULT_TOTAL_CONCURRENT_REQUESTS,
    p95_budget_ms: float = P95_BUDGET_MS,
    max_budget_ms: float = MAX_BUDGET_MS,
    concurrent_p95_budget_ms: float = CONCURRENT_P95_BUDGET_MS,
) -> dict[str, Any]:
    client, first_job_id, cleanup = _build_client()
    headers = _auth_headers()
    results: list[PolicyResult] = [
        PolicyResult("seeded_realistic_job_volume", SEED_JOB_COUNT >= 250, f"{SEED_JOB_COUNT} jobs seeded"),
        PolicyResult("performance_budget_configured", p95_budget_ms > 0 and max_budget_ms > p95_budget_ms, f"p95={p95_budget_ms}ms max={max_budget_ms}ms"),
    ]
    endpoint_specs = [
        ("health", "GET", "/health", None),
        ("version", "GET", "/version", None),
        ("metrics", "GET", "/metrics", None),
        ("slo", "GET", "/slo", None),
        ("jobs_list", "GET", "/jobs?limit=25", headers),
        ("job_detail", "GET", f"/jobs/{first_job_id}", headers),
        ("audit", "GET", "/audit?limit=10", headers),
        ("audit_verify", "GET", "/audit/verify", headers),
    ]
    payloads: dict[str, Any] = {}

    try:
        for name, method, path, request_headers in endpoint_specs:
            stats, payload = _run_sequential(
                client,
                method,
                path,
                headers=request_headers,
                sample_count=sample_count,
            )
            payloads[name] = payload
            results.extend([
                PolicyResult(f"{name}_success", stats.all_success, _stats_detail(stats, p95_budget_ms)),
                PolicyResult(f"{name}_p95_budget", stats.p95_ms <= p95_budget_ms, _stats_detail(stats, p95_budget_ms)),
                PolicyResult(f"{name}_max_budget", stats.max_ms <= max_budget_ms, _stats_detail(stats, max_budget_ms)),
            ])

        bounded_status, _bounded_ms, bounded_payload = _measure(client, "GET", "/jobs?limit=500", headers=headers)
        results.append(PolicyResult("job_list_high_limit_success", bounded_status == 200, f"status={bounded_status}"))
        results.append(
            PolicyResult(
                "job_list_high_limit_is_bounded",
                isinstance(bounded_payload, list) and len(bounded_payload) <= MAX_JOB_LIST_LIMIT,
                f"returned={len(bounded_payload) if isinstance(bounded_payload, list) else 'not-list'} max={MAX_JOB_LIST_LIMIT}",
            )
        )

        concurrent_jobs = _run_concurrent(
            client,
            "GET",
            "/jobs?limit=25",
            headers=headers,
            total_requests=total_concurrent_requests,
            concurrency=concurrency,
        )
        results.extend([
            PolicyResult("concurrent_jobs_success", concurrent_jobs.all_success, _stats_detail(concurrent_jobs, concurrent_p95_budget_ms)),
            PolicyResult("concurrent_jobs_p95_budget", concurrent_jobs.p95_ms <= concurrent_p95_budget_ms, _stats_detail(concurrent_jobs, concurrent_p95_budget_ms)),
        ])

        concurrent_metrics = _run_concurrent(
            client,
            "GET",
            "/metrics",
            headers=None,
            total_requests=total_concurrent_requests,
            concurrency=concurrency,
        )
        results.extend([
            PolicyResult("concurrent_metrics_success", concurrent_metrics.all_success, _stats_detail(concurrent_metrics, concurrent_p95_budget_ms)),
            PolicyResult("concurrent_metrics_p95_budget", concurrent_metrics.p95_ms <= concurrent_p95_budget_ms, _stats_detail(concurrent_metrics, concurrent_p95_budget_ms)),
        ])

        metrics_status, _metrics_ms, metrics_payload = _measure(client, "GET", "/metrics")
        runtime = metrics_payload.get("runtime", {}) if metrics_status == 200 and isinstance(metrics_payload, dict) else {}
        counters = runtime.get("counters", {})
        histograms = runtime.get("histograms", {})
        results.extend([
            PolicyResult("metrics_after_load_success", metrics_status == 200, f"status={metrics_status}"),
            PolicyResult(
                "request_counter_emitted",
                any(key.startswith("autoops_api_requests_total") for key in counters),
                f"{len(counters)} counters",
            ),
            PolicyResult(
                "latency_histogram_emitted",
                any(key.startswith("autoops_api_request_duration") for key in histograms),
                f"{len(histograms)} histograms",
            ),
            PolicyResult(
                "jobs_payload_shape_stable",
                isinstance(payloads.get("jobs_list"), list)
                and len(payloads["jobs_list"]) == 25
                and {"id", "goal", "status", "current_step", "trace", "result"}.issubset(payloads["jobs_list"][0]),
                "GET /jobs?limit=25 returns 25 JobResponse-compatible rows",
            ),
        ])
    finally:
        cleanup()

    failed = [result for result in results if not result.ok]
    durations = [
        float(part.split("=", 1)[1].removesuffix("ms"))
        for result in results
        for part in result.detail.split()
        if part.startswith("p95=")
    ]
    return {
        "ok": not failed,
        "passed": len(results) - len(failed),
        "failed": len(failed),
        "checks": [asdict(result) for result in results],
        "summary": {
            "sample_count": sample_count,
            "concurrency": concurrency,
            "total_concurrent_requests": total_concurrent_requests,
            "median_observed_p95_ms": statistics.median(durations) if durations else 0.0,
            "p95_budget_ms": p95_budget_ms,
            "concurrent_p95_budget_ms": concurrent_p95_budget_ms,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run AutoOps synthetic API performance policy checks.")
    parser.add_argument("--sample-count", type=int, default=DEFAULT_SAMPLE_COUNT)
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    parser.add_argument("--total-concurrent-requests", type=int, default=DEFAULT_TOTAL_CONCURRENT_REQUESTS)
    args = parser.parse_args(argv)

    summary = run_performance_policy_checks(
        sample_count=max(1, args.sample_count),
        concurrency=max(1, args.concurrency),
        total_concurrent_requests=max(1, args.total_concurrent_requests),
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
