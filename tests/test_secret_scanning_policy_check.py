from scripts import secret_scanning_policy_check


def test_secret_scanning_policy_current_repository_passes():
    summary = secret_scanning_policy_check.run_secret_scanning_policy_checks()

    assert summary["ok"] is True
    assert summary["failed"] == 0
    assert {check["check"] for check in summary["checks"]} >= {
        "gitleaks_image_pinned",
        "redacted_output",
        "release_gate_dependency",
        "pre_commit_version_pinned",
    }


def test_secret_scanning_policy_rejects_unpinned_unredacted_scan(tmp_path):
    workflow = tmp_path / "ci.yml"
    workflow.write_text(
        """
name: CI
on: [push]
jobs:
  secret-scan:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: docker run ghcr.io/gitleaks/gitleaks:latest dir .
  release-gate:
    runs-on: ubuntu-latest
    steps: []
""",
        encoding="utf-8",
    )
    pre_commit = tmp_path / ".pre-commit-config.yaml"
    pre_commit.write_text(
        """
repos:
  - repo: https://github.com/gitleaks/gitleaks
    rev: master
    hooks:
      - id: gitleaks
""",
        encoding="utf-8",
    )

    summary = secret_scanning_policy_check.run_secret_scanning_policy_checks(workflow, pre_commit)
    failed = {check["check"] for check in summary["checks"] if not check["ok"]}

    assert summary["ok"] is False
    assert "full_history_checkout" in failed
    assert "gitleaks_image_pinned" in failed
    assert "redacted_output" in failed
    assert "release_gate_dependency" in failed
    assert "pre_commit_version_pinned" in failed
