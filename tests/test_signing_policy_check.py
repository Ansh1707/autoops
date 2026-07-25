from scripts import signing_policy_check


def test_signing_policy_current_workflow_passes():
    summary = signing_policy_check.run_signing_policy_checks()

    assert summary["ok"] is True
    assert summary["failed"] == 0
    checks = {check["check"] for check in summary["checks"]}
    assert {
        "image_release_job_exists",
        "scoped_write_permissions",
        "cosign_installed",
        "api_cosign_signs_digest",
        "worker_cosign_signs_digest",
        "frontend_cosign_signs_digest",
        "signing_artifacts_uploaded",
    }.issubset(checks)


def test_signing_policy_rejects_unsigned_image_release(tmp_path):
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
  image-release:
    needs: release-gate
    if: github.event_name == 'push'
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write
    steps:
      - uses: actions/checkout@v4
      - uses: docker/build-push-action@v6
        with:
          context: .
          file: Dockerfile.api
          push: true
""",
        encoding="utf-8",
    )

    summary = signing_policy_check.run_signing_policy_checks(workflow)

    assert summary["ok"] is False
    failed = {check["check"] for check in summary["checks"] if not check["ok"]}
    assert "scoped_write_permissions" in failed
    assert "cosign_installed" in failed
    assert "api_cosign_signs_digest" in failed
