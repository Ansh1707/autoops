"""Static CI workflow policy checks for AutoOps."""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from dataclasses import asdict, dataclass
from typing import Any

import yaml


ROOT = pathlib.Path(__file__).resolve().parents[1]
CI_FILE = ROOT / ".github" / "workflows" / "ci.yml"


@dataclass(frozen=True)
class PolicyResult:
    check: str
    ok: bool
    detail: str


def _load_workflow(path: pathlib.Path = CI_FILE) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _workflow_triggers(workflow: dict[str, Any]) -> Any:
    return workflow.get("on", workflow.get(True, {}))


def _trigger_names(triggers: Any) -> set[str]:
    if isinstance(triggers, str):
        return {triggers}
    if isinstance(triggers, list):
        return {str(trigger) for trigger in triggers}
    if isinstance(triggers, dict):
        return {str(trigger) for trigger in triggers}
    return set()


def _run_commands(steps: list[dict[str, Any]]) -> list[str]:
    return [str(step.get("run", "")) for step in steps if step.get("run")]


def _uses_action(steps: list[dict[str, Any]], action_prefix: str) -> bool:
    return any(str(step.get("uses", "")).startswith(action_prefix) for step in steps)


def _display_path(path: pathlib.Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def run_ci_policy_checks(path: pathlib.Path = CI_FILE) -> dict[str, Any]:
    if not path.exists():
        result = PolicyResult("workflow_exists", False, f"missing workflow: {path}")
        return {"ok": False, "passed": 0, "failed": 1, "checks": [asdict(result)]}

    workflow = _load_workflow(path)
    triggers = _trigger_names(_workflow_triggers(workflow))
    permissions = workflow.get("permissions") or {}
    jobs = workflow.get("jobs") or {}
    release_job = jobs.get("release-gate") or jobs.get("test") or {}
    steps = release_job.get("steps") or []
    services = release_job.get("services") or {}
    commands = _run_commands(steps)
    all_commands = "\n".join(commands)

    results = [
        PolicyResult("workflow_exists", True, _display_path(path)),
        PolicyResult(
            "required_triggers",
            {"push", "pull_request", "workflow_dispatch"}.issubset(triggers),
            ", ".join(sorted(triggers)) or "no triggers",
        ),
        PolicyResult(
            "least_privilege_permissions",
            permissions.get("contents") == "read" and len(permissions) == 1,
            json.dumps(permissions, sort_keys=True),
        ),
        PolicyResult("concurrency_control", bool(workflow.get("concurrency")), "workflow cancels superseded runs"),
        PolicyResult("job_timeout", bool(release_job.get("timeout-minutes")), "release job has timeout-minutes"),
        PolicyResult(
            "required_services",
            {"postgres", "redis"}.issubset(set(services)),
            ", ".join(sorted(services)) or "no services",
        ),
        PolicyResult("python_setup", _uses_action(steps, "actions/setup-python@v5"), "uses actions/setup-python@v5"),
        PolicyResult("node_setup", _uses_action(steps, "actions/setup-node@v4"), "uses actions/setup-node@v4"),
        PolicyResult("npm_ci", "npm ci" in all_commands, "frontend dependencies installed with npm ci"),
        PolicyResult("release_gate_runs", "python scripts/release_check.py" in all_commands, "full release gate runs in CI"),
        PolicyResult(
            "release_manifest_artifact",
            "scripts/release_manifest.py --output reports/release-manifest.json" in all_commands
            and _uses_action(steps, "actions/upload-artifact@v4"),
            "CI uploads release-manifest.json as an artifact",
        ),
    ]
    failed = [result for result in results if not result.ok]
    return {
        "ok": not failed,
        "passed": len(results) - len(failed),
        "failed": len(failed),
        "checks": [asdict(result) for result in results],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check AutoOps CI workflow policy.")
    parser.add_argument("file", nargs="?", type=pathlib.Path, default=CI_FILE)
    args = parser.parse_args(argv)
    try:
        summary = run_ci_policy_checks(args.file)
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0 if summary["ok"] else 1
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
