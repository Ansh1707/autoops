from api.metrics import JobsMetrics, MetricsRegistry
from api.slo import evaluate_slos


def test_evaluate_slos_passes_for_healthy_metrics(monkeypatch):
    registry = MetricsRegistry()
    registry.observe("autoops_api_request_duration", 0.1, path="/health", method="GET")
    metrics = JobsMetrics(
        jobs_total=10,
        jobs_active=1,
        jobs_terminal=9,
        jobs_by_status={"SUCCESS": 9, "FAILED": 1},
        audit_events_total=0,
    )
    monkeypatch.setenv("AUTOOPS_SLO_MAX_FAILED_JOB_RATIO", "0.2")

    result = evaluate_slos(metrics, registry)

    assert result["ok"] is True
    assert result["failed"] == 0


def test_evaluate_slos_fails_for_backlog_failures_and_latency(monkeypatch):
    registry = MetricsRegistry()
    registry.observe("autoops_api_request_duration", 3.0, path="/slow", method="GET")
    metrics = JobsMetrics(
        jobs_total=4,
        jobs_active=25,
        jobs_terminal=4,
        jobs_by_status={"SUCCESS": 1, "FAILED": 3},
        audit_events_total=0,
    )
    monkeypatch.setenv("AUTOOPS_SLO_MAX_ACTIVE_JOBS", "20")
    monkeypatch.setenv("AUTOOPS_SLO_MAX_FAILED_JOB_RATIO", "0.25")
    monkeypatch.setenv("AUTOOPS_SLO_MAX_P95_LATENCY_MS", "2000")

    result = evaluate_slos(metrics, registry)

    failed_names = {objective["name"] for objective in result["objectives"] if not objective["ok"]}
    assert result["ok"] is False
    assert failed_names == {"active_job_backlog", "failed_job_ratio", "api_request_latency_p95_ms"}
