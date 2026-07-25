from scripts import release_check


def test_build_checks_include_required_release_gates():
    names = [name for name, _command, _cwd in release_check.build_checks()]

    assert names == [
        "python_compile",
        "pytest",
        "agent_evals",
        "container_security",
        "dependency_policy",
        "secret_scanning_policy",
        "publication_policy",
        "ci_policy",
        "vulnerability_policy",
        "signing_policy",
        "deployment_policy",
        "frontend_dashboard_policy",
        "api_contract_policy",
        "performance_policy",
        "migration_policy",
        "resilience_policy",
        "k8s_policy",
        "alert_policy",
        "dr_drill",
        "release_manifest",
        "docker_compose_config",
        "frontend_lint",
        "frontend_build",
    ]


def test_python_files_skip_dependency_directories():
    files = release_check.python_files()

    assert "scripts/release_check.py" in files
    assert not any("node_modules" in path for path in files)
    assert not any(path.startswith("venv/") for path in files)


def test_release_check_summary_reports_failure(monkeypatch):
    calls = []

    def fake_run_command(name, command, cwd):
        calls.append(name)
        return release_check.CheckResult(
            name=name,
            ok=name != "pytest",
            duration_ms=1,
            command=command,
            returncode=0 if name != "pytest" else 1,
            output="",
        )

    monkeypatch.setattr(release_check, "run_command", fake_run_command)

    summary = release_check.run_release_checks(skip_frontend=True, skip_docker=True)

    assert calls == [
        "python_compile",
        "pytest",
        "agent_evals",
        "container_security",
        "dependency_policy",
        "secret_scanning_policy",
        "publication_policy",
        "ci_policy",
        "vulnerability_policy",
        "signing_policy",
        "deployment_policy",
        "frontend_dashboard_policy",
        "api_contract_policy",
        "performance_policy",
        "migration_policy",
        "resilience_policy",
        "k8s_policy",
        "alert_policy",
        "dr_drill",
        "release_manifest",
    ]
    assert summary["ok"] is False
    assert summary["passed"] == 19
    assert summary["failed"] == 1
