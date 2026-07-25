from scripts import alert_policy_check


def test_alert_policy_current_rules_pass():
    summary = alert_policy_check.run_alert_policy_checks()

    assert summary["ok"] is True
    assert summary["failed"] == 0
    assert summary["alerts"] >= len(alert_policy_check.REQUIRED_ALERTS)
    assert any(
        check["check"] == "runbook_is_actionable" and check["ok"]
        for check in summary["checks"]
    )


def test_alert_policy_rejects_missing_runbook_and_required_alerts(tmp_path):
    rules = tmp_path / "alerts.yaml"
    rules.write_text(
        """
apiVersion: monitoring.coreos.com/v1
kind: PrometheusRule
metadata:
  name: autoops-alerts
spec:
  groups:
    - name: autoops.slo
      rules:
        - alert: AutoOpsApiDown
          expr: up == 0
          labels:
            severity: page
          annotations:
            summary: API down
""",
        encoding="utf-8",
    )

    summary = alert_policy_check.run_alert_policy_checks(rules)
    failed_checks = {check["check"] for check in summary["checks"] if not check["ok"]}

    assert summary["ok"] is False
    assert "required_alerts_present" in failed_checks
    assert "has_for_duration" in failed_checks
    assert "has_severity" in failed_checks
    assert "has_runbook" in failed_checks
    assert "runbook_is_actionable" in failed_checks


def test_alert_policy_rejects_incomplete_local_runbook(tmp_path):
    runbook = tmp_path / "runbook.md"
    runbook.write_text("# Broken\n\n## Impact\n\nToo thin.\n", encoding="utf-8")
    rules = tmp_path / "alerts.yaml"
    rules.write_text(
        f"""
apiVersion: monitoring.coreos.com/v1
kind: PrometheusRule
metadata:
  name: autoops-alerts
spec:
  groups:
    - name: autoops.slo
      rules:
        - alert: AutoOpsApiDown
          expr: up == 0
          for: 1m
          labels:
            severity: critical
          annotations:
            summary: API down
            runbook_url: {runbook}
""",
        encoding="utf-8",
    )

    summary = alert_policy_check.run_alert_policy_checks(rules)
    actionable = [
        check for check in summary["checks"]
        if check["alert"] == "AutoOpsApiDown" and check["check"] == "runbook_is_actionable"
    ][0]

    assert actionable["ok"] is False
    assert "missing sections" in actionable["detail"]
