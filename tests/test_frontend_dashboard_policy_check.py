from scripts import frontend_dashboard_policy_check


def test_frontend_dashboard_policy_current_app_passes():
    summary = frontend_dashboard_policy_check.run_frontend_dashboard_policy_checks()

    assert summary["ok"] is True
    assert summary["failed"] == 0
    checks = {check["check"] for check in summary["checks"]}
    assert {
        "ui_operations_dashboard",
        "endpoint_/metrics",
        "endpoint_/slo",
        "endpoint_/jobs?limit=12",
        "auto_refresh",
        "live_job_inventory",
    }.issubset(checks)


def test_frontend_dashboard_policy_rejects_missing_operations_surface(tmp_path):
    app = tmp_path / "App.tsx"
    css = tmp_path / "index.css"
    app.write_text("export default function App() { return <main /> }", encoding="utf-8")
    css.write_text(".app-shell { display: grid; }", encoding="utf-8")

    summary = frontend_dashboard_policy_check.run_frontend_dashboard_policy_checks(app, css)

    assert summary["ok"] is False
    failed = {check["check"] for check in summary["checks"] if not check["ok"]}
    assert "ui_operations_dashboard" in failed
    assert "endpoint_/metrics" in failed
    assert "css_dashboard-grid" in failed
