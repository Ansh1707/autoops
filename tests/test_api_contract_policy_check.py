from scripts import api_contract_policy_check


def test_api_contract_policy_current_app_passes_without_snapshot():
    summary = api_contract_policy_check.run_api_contract_policy_checks(require_snapshot=False)

    assert summary["ok"] is True
    assert summary["failed"] == 0
    checks = {check["check"] for check in summary["checks"]}
    assert "protected_bearer_get_/jobs" in checks
    assert "public_no_auth_post_/token" in checks
    assert "job_response_field_current_step" in checks


def test_api_contract_policy_rejects_missing_protected_route(monkeypatch):
    spec = api_contract_policy_check.load_openapi()
    broken = {
        **spec,
        "paths": {
            path: operations
            for path, operations in spec["paths"].items()
            if path != "/jobs"
        },
    }

    monkeypatch.setattr(api_contract_policy_check, "load_openapi", lambda: broken)

    summary = api_contract_policy_check.run_api_contract_policy_checks(require_snapshot=False)

    assert summary["ok"] is False
    failed = {check["check"] for check in summary["checks"] if not check["ok"]}
    assert "path_/jobs" in failed
    assert "operation_get_/jobs" in failed
    assert "protected_bearer_get_/jobs" in failed


def test_api_contract_policy_rejects_auth_removed_from_private_route(monkeypatch):
    spec = api_contract_policy_check.load_openapi()
    broken = {
        **spec,
        "paths": {
            **spec["paths"],
            "/investigate": {
                **spec["paths"]["/investigate"],
                "post": {
                    key: value
                    for key, value in spec["paths"]["/investigate"]["post"].items()
                    if key != "security"
                },
            },
        },
    }

    monkeypatch.setattr(api_contract_policy_check, "load_openapi", lambda: broken)

    summary = api_contract_policy_check.run_api_contract_policy_checks(require_snapshot=False)

    assert summary["ok"] is False
    failed = {check["check"] for check in summary["checks"] if not check["ok"]}
    assert "protected_bearer_post_/investigate" in failed
