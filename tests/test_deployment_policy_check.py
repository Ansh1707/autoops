from scripts import deployment_policy_check


def test_deployment_policy_current_workflow_passes():
    summary = deployment_policy_check.run_deployment_policy_checks()

    assert summary["ok"] is True
    assert summary["failed"] == 0
    checks = {check["check"] for check in summary["checks"]}
    assert {
        "deployment_proof_job_exists",
        "kind_cluster_created",
        "local_images_loaded",
        "rollouts_waited",
        "api_smoke_checked",
        "deployment_evidence_uploaded",
    }.issubset(checks)


def test_deployment_policy_rejects_workflow_without_kind_proof(tmp_path):
    workflow = tmp_path / "ci.yml"
    workflow.write_text(
        """
name: CI
on:
  push:
jobs:
  release-gate:
    runs-on: ubuntu-latest
    steps:
      - run: python scripts/release_check.py
""",
        encoding="utf-8",
    )

    summary = deployment_policy_check.run_deployment_policy_checks(workflow)

    assert summary["ok"] is False
    failed = {check["check"] for check in summary["checks"] if not check["ok"]}
    assert "deployment_proof_job_exists" in failed
    assert "kind_cluster_created" in failed
    assert "api_smoke_checked" in failed
