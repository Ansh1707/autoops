from scripts import performance_policy_check


def test_performance_policy_current_app_passes_small_profile():
    summary = performance_policy_check.run_performance_policy_checks(
        sample_count=2,
        concurrency=2,
        total_concurrent_requests=4,
    )

    assert summary["ok"] is True
    assert summary["failed"] == 0
    checks = {check["check"] for check in summary["checks"]}
    assert "jobs_payload_shape_stable" in checks
    assert "request_counter_emitted" in checks
    assert "concurrent_jobs_p95_budget" in checks


def test_performance_policy_rejects_impossible_latency_budget():
    summary = performance_policy_check.run_performance_policy_checks(
        sample_count=1,
        concurrency=1,
        total_concurrent_requests=1,
        p95_budget_ms=0.000001,
        max_budget_ms=0.000002,
        concurrent_p95_budget_ms=0.000001,
    )

    assert summary["ok"] is False
    failed = {check["check"] for check in summary["checks"] if not check["ok"]}
    assert "health_p95_budget" in failed
    assert "concurrent_jobs_p95_budget" in failed
