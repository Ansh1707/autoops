from scripts import resilience_policy_check


def test_resilience_policy_current_app_passes():
    summary = resilience_policy_check.run_resilience_policy_checks()

    assert summary["ok"] is True
    assert summary["failed"] == 0
    checks = {check["check"] for check in summary["checks"]}
    assert "optional_outages_do_not_block_readiness" in checks
    assert "redis_outage_blocks_required_readiness" in checks
    assert "session_memory_degrades_when_redis_fails" in checks


def test_resilience_policy_rejects_session_memory_exception(monkeypatch):
    def broken_save_step(*args, **kwargs):
        raise RuntimeError("session memory regression")

    monkeypatch.setattr(resilience_policy_check.session_memory, "save_step", broken_save_step)

    summary = resilience_policy_check.run_resilience_policy_checks()

    assert summary["ok"] is False
    failed = {check["check"] for check in summary["checks"] if not check["ok"]}
    assert "session_memory_degrades_when_redis_fails" in failed
