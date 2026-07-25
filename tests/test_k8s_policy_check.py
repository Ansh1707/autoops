from scripts import k8s_policy_check


def test_k8s_policy_current_manifest_passes():
    summary = k8s_policy_check.run_k8s_policy_checks()

    assert summary["ok"] is True
    assert summary["failed"] == 0
    assert summary["resources"] > 10
    assert any(
        check["check"] == "prometheus_scrape_annotation" and check["ok"]
        for check in summary["checks"]
    )


def test_k8s_policy_rejects_unsafe_workload(tmp_path):
    manifest = tmp_path / "unsafe.yaml"
    manifest.write_text(
        """
apiVersion: apps/v1
kind: Deployment
metadata:
  name: unsafe
spec:
  selector:
    matchLabels:
      app: unsafe
  template:
    metadata:
      labels:
        app: unsafe
    spec:
      containers:
        - name: api
          image: autoops-api:latest
""",
        encoding="utf-8",
    )

    summary = k8s_policy_check.run_k8s_policy_checks([manifest])
    failed_checks = {check["check"] for check in summary["checks"] if not check["ok"]}

    assert summary["ok"] is False
    assert "image_pinned" in failed_checks
    assert "resources_set" in failed_checks
    assert "pod_runs_non_root" in failed_checks
    assert "service_account_token_disabled" in failed_checks


def test_k8s_policy_rejects_committed_secret(tmp_path):
    manifest = tmp_path / "secret.yaml"
    manifest.write_text(
        """
apiVersion: v1
kind: Secret
metadata:
  name: autoops-secrets
stringData:
  postgres-password: postgres
""",
        encoding="utf-8",
    )

    summary = k8s_policy_check.run_k8s_policy_checks([manifest])
    failed_checks = {check["check"] for check in summary["checks"] if not check["ok"]}

    assert "no_committed_secret_resources" in failed_checks
    assert "no_secret_literals" in failed_checks
