from scripts import ci_policy_check


def test_ci_policy_current_workflow_passes():
    summary = ci_policy_check.run_ci_policy_checks()

    assert summary["ok"] is True
    assert summary["failed"] == 0
    assert {check["check"] for check in summary["checks"]} >= {
        "least_privilege_permissions",
        "concurrency_control",
        "release_gate_runs",
        "release_manifest_artifact",
    }


def test_ci_policy_rejects_workflow_without_release_gate(tmp_path):
    workflow = tmp_path / "ci.yml"
    workflow.write_text(
        """
name: CI
on:
  push:
  pull_request:
  workflow_dispatch:
permissions:
  contents: read
concurrency:
  group: test
jobs:
  release-gate:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    services:
      postgres:
        image: postgres:15-alpine
      redis:
        image: redis:7-alpine
    steps:
      - uses: actions/setup-python@v5
      - uses: actions/setup-node@v4
      - run: npm ci
""",
        encoding="utf-8",
    )

    summary = ci_policy_check.run_ci_policy_checks(workflow)

    assert summary["ok"] is False
    failed = {check["check"] for check in summary["checks"] if not check["ok"]}
    assert "release_gate_runs" in failed
    assert "release_manifest_artifact" in failed
