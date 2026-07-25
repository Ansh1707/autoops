"""Static policy checks for AutoOps Prometheus alert rules."""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from dataclasses import asdict, dataclass
from typing import Any

import yaml


ROOT = pathlib.Path(__file__).resolve().parents[1]
ALERT_FILE = ROOT / "k8s" / "prometheus-alerts.yaml"
REQUIRED_ALERTS = {
    "AutoOpsApiDown",
    "AutoOpsHighApiErrorRate",
    "AutoOpsHighApiLatency",
    "AutoOpsRateLimitPressure",
    "AutoOpsActiveJobBacklog",
    "AutoOpsFailedJobsIncreasing",
}
REQUIRED_RUNBOOK_SECTIONS = {
    "## Impact",
    "## Triage",
    "## Mitigation",
    "## Recovery Validation",
    "## Escalation",
    "## Prevention",
}


@dataclass(frozen=True)
class PolicyResult:
    alert: str
    check: str
    ok: bool
    detail: str


def load_alert_rules(path: pathlib.Path = ALERT_FILE) -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = []
    for document in yaml.safe_load_all(path.read_text(encoding="utf-8")):
        if not document:
            continue
        if document.get("kind") != "PrometheusRule":
            continue
        for group in ((document.get("spec") or {}).get("groups") or []):
            rules.extend(group.get("rules") or [])
    return rules


def _resolve_runbook(value: str) -> pathlib.Path | None:
    if not value or value.startswith(("http://", "https://")):
        return None
    path = pathlib.Path(value)
    if not path.is_absolute():
        path = ROOT / path
    return path


def _runbook_sections_ok(path: pathlib.Path) -> tuple[bool, str]:
    if not path.exists():
        return False, f"runbook file missing: {path.relative_to(ROOT) if path.is_relative_to(ROOT) else path}"
    text = path.read_text(encoding="utf-8")
    missing = sorted(section for section in REQUIRED_RUNBOOK_SECTIONS if section not in text)
    if missing:
        return False, "missing sections: " + ", ".join(missing)
    return True, f"runbook file exists with {len(REQUIRED_RUNBOOK_SECTIONS)} required sections"


def run_alert_policy_checks(path: pathlib.Path = ALERT_FILE) -> dict:
    rules = load_alert_rules(path)
    alerts = {rule.get("alert"): rule for rule in rules if rule.get("alert")}
    results: list[PolicyResult] = []

    missing = sorted(REQUIRED_ALERTS - set(alerts))
    results.append(
        PolicyResult(
            "alert-set",
            "required_alerts_present",
            not missing,
            ", ".join(missing) or f"{len(REQUIRED_ALERTS)} required alerts present",
        )
    )

    for name, rule in sorted(alerts.items()):
        labels = rule.get("labels") or {}
        annotations = rule.get("annotations") or {}
        expr = str(rule.get("expr") or "")
        runbook = str(annotations.get("runbook_url", ""))
        runbook_path = _resolve_runbook(runbook)
        runbook_ok, runbook_detail = (
            _runbook_sections_ok(runbook_path)
            if runbook_path
            else (runbook.startswith("https://"), "runbook_url must be https:// or a local repo path")
        )
        results.extend([
            PolicyResult(name, "has_expression", bool(expr.strip()), "alert has a PromQL expression"),
            PolicyResult(name, "has_for_duration", bool(rule.get("for")), "alert has a for duration"),
            PolicyResult(name, "has_severity", labels.get("severity") in {"warning", "critical"}, "severity is warning or critical"),
            PolicyResult(name, "has_summary", bool(annotations.get("summary")), "summary annotation is present"),
            PolicyResult(name, "has_runbook", bool(runbook), "runbook_url annotation is present"),
            PolicyResult(name, "runbook_is_actionable", runbook_ok, runbook_detail),
        ])

    failed = [result for result in results if not result.ok]
    return {
        "ok": not failed,
        "passed": len(results) - len(failed),
        "failed": len(failed),
        "alerts": len(alerts),
        "checks": [asdict(result) for result in results],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check AutoOps Prometheus alert rule coverage.")
    parser.add_argument("file", nargs="?", type=pathlib.Path, default=ALERT_FILE)
    args = parser.parse_args(argv)
    try:
        summary = run_alert_policy_checks(args.file)
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0 if summary["ok"] else 1
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
