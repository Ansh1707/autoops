"""Static policy checks for AutoOps secret scanning controls."""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from dataclasses import asdict, dataclass
from typing import Any

import yaml


ROOT = pathlib.Path(__file__).resolve().parents[1]
CI_FILE = ROOT / ".github" / "workflows" / "ci.yml"
PRE_COMMIT_FILE = ROOT / ".pre-commit-config.yaml"
GITLEAKS_VERSION = "v8.30.1"


@dataclass(frozen=True)
class PolicyResult:
    check: str
    ok: bool
    detail: str


def _load_yaml(path: pathlib.Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    return [step for step in job.get("steps", []) if isinstance(step, dict)]


def _all_commands(job: dict[str, Any]) -> str:
    return "\n".join(str(step.get("run", "")) for step in _steps(job) if step.get("run"))


def _checkout_has_full_history(job: dict[str, Any]) -> bool:
    for step in _steps(job):
        if not str(step.get("uses", "")).startswith("actions/checkout@"):
            continue
        values = step.get("with") or {}
        if str(values.get("fetch-depth")) == "0":
            return True
    return False


def _pre_commit_hook(config: dict[str, Any]) -> dict[str, Any] | None:
    for repository in config.get("repos", []):
        if repository.get("repo") != "https://github.com/gitleaks/gitleaks":
            continue
        for hook in repository.get("hooks", []):
            if hook.get("id") == "gitleaks":
                return {"repository": repository, "hook": hook}
    return None


def run_secret_scanning_policy_checks(
    ci_path: pathlib.Path = CI_FILE,
    pre_commit_path: pathlib.Path = PRE_COMMIT_FILE,
) -> dict[str, Any]:
    if not ci_path.exists() or not pre_commit_path.exists():
        missing = [str(path) for path in (ci_path, pre_commit_path) if not path.exists()]
        result = PolicyResult("required_files", False, "missing: " + ", ".join(missing))
        return {"ok": False, "passed": 0, "failed": 1, "checks": [asdict(result)]}

    workflow = _load_yaml(ci_path)
    jobs = workflow.get("jobs") or {}
    scan_job = jobs.get("secret-scan") or {}
    release_job = jobs.get("release-gate") or {}
    commands = _all_commands(scan_job)
    pre_commit = _load_yaml(pre_commit_path)
    hook_info = _pre_commit_hook(pre_commit)
    hook_repository = hook_info["repository"] if hook_info else {}
    hook = hook_info["hook"] if hook_info else {}
    hook_args = {str(value) for value in hook.get("args", [])}

    image_match = re.search(r"ghcr\.io/gitleaks/gitleaks:(v?\d+\.\d+\.\d+)", commands)
    image_version = image_match.group(1) if image_match else ""
    release_needs = release_job.get("needs")
    if isinstance(release_needs, str):
        release_dependencies = {release_needs}
    else:
        release_dependencies = set(release_needs or [])

    results = [
        PolicyResult("secret_scan_job", bool(scan_job), "dedicated secret-scan CI job exists"),
        PolicyResult(
            "full_history_checkout",
            _checkout_has_full_history(scan_job),
            "secret scanner receives complete Git history",
        ),
        PolicyResult(
            "gitleaks_image_pinned",
            image_version == GITLEAKS_VERSION,
            image_version or "missing explicit Gitleaks image version",
        ),
        PolicyResult(
            "gitleaks_git_scan",
            bool(re.search(r"gitleaks:v?\d+\.\d+\.\d+\s+\\\\?\s*git\s+/repo", commands)),
            "CI scans repository history instead of only the working directory",
        ),
        PolicyResult(
            "redacted_output",
            "--redact" in commands and "--no-banner" in commands,
            "secret values are redacted from CI output",
        ),
        PolicyResult(
            "decoded_and_archived_content",
            "--max-decode-depth=2" in commands and "--max-archive-depth=1" in commands,
            "CI scans encoded values and one archive layer",
        ),
        PolicyResult(
            "release_gate_dependency",
            "secret-scan" in release_dependencies,
            "release gate cannot run until secret scanning passes",
        ),
        PolicyResult("pre_commit_hook", hook_info is not None, "Gitleaks pre-commit hook is configured"),
        PolicyResult(
            "pre_commit_version_pinned",
            hook_repository.get("rev") == GITLEAKS_VERSION,
            str(hook_repository.get("rev") or "missing"),
        ),
        PolicyResult(
            "pre_commit_redaction",
            {"--redact", "--no-banner"}.issubset(hook_args),
            "local hook redacts findings",
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
    parser = argparse.ArgumentParser(description="Check AutoOps secret scanning policy.")
    parser.add_argument("--ci-file", type=pathlib.Path, default=CI_FILE)
    parser.add_argument("--pre-commit-file", type=pathlib.Path, default=PRE_COMMIT_FILE)
    args = parser.parse_args(argv)
    try:
        summary = run_secret_scanning_policy_checks(args.ci_file, args.pre_commit_file)
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0 if summary["ok"] else 1
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
