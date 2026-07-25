"""Policy checks for signed AutoOps container image releases."""

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
IMAGE_NAMES = ("api", "worker", "frontend")


@dataclass(frozen=True)
class PolicyResult:
    check: str
    ok: bool
    detail: str


def _load_workflow(path: pathlib.Path = CI_FILE) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _uses_action(steps: list[dict[str, Any]], action_prefix: str) -> bool:
    return any(str(step.get("uses", "")).startswith(action_prefix) for step in steps)


def _step_text(steps: list[dict[str, Any]]) -> str:
    return json.dumps(steps)


def _run_text(steps: list[dict[str, Any]]) -> str:
    return "\n".join(str(step.get("run", "")) for step in steps if step.get("run"))


def _build_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        step for step in steps
        if str(step.get("uses", "")).startswith("docker/build-push-action@v6")
    ]


def _build_step_has(step: dict[str, Any], text: str) -> bool:
    return text in json.dumps(step.get("with") or {})


def run_signing_policy_checks(path: pathlib.Path = CI_FILE) -> dict[str, Any]:
    if not path.exists():
        result = PolicyResult("workflow_exists", False, f"missing workflow: {path}")
        return {"ok": False, "passed": 0, "failed": 1, "checks": [asdict(result)]}

    workflow = _load_workflow(path)
    jobs = workflow.get("jobs") or {}
    release_job = jobs.get("image-release") or {}
    steps = release_job.get("steps") or []
    permissions = release_job.get("permissions") or {}
    job_if = str(release_job.get("if", ""))
    run_text = _run_text(steps)
    step_text = _step_text(steps)
    build_steps = _build_steps(steps)

    results = [
        PolicyResult("image_release_job_exists", bool(release_job), "image-release job is defined"),
        PolicyResult("release_gate_dependency", release_job.get("needs") == "release-gate", "image release waits for release-gate"),
        PolicyResult(
            "trusted_push_only",
            "github.event_name == 'push'" in job_if
            and "refs/heads/main" in job_if
            and "refs/tags/" in job_if,
            job_if or "missing job if condition",
        ),
        PolicyResult(
            "scoped_write_permissions",
            permissions.get("contents") == "read"
            and permissions.get("packages") == "write"
            and permissions.get("id-token") == "write"
            and len(permissions) == 3,
            json.dumps(permissions, sort_keys=True),
        ),
        PolicyResult("ghcr_login", _uses_action(steps, "docker/login-action@v3"), "logs into GHCR with GITHUB_TOKEN"),
        PolicyResult("buildx_enabled", _uses_action(steps, "docker/setup-buildx-action@v3"), "uses Docker Buildx"),
        PolicyResult("cosign_installed", _uses_action(steps, "sigstore/cosign-installer@v3"), "installs Cosign"),
        PolicyResult("three_images_built", len(build_steps) == 3, f"{len(build_steps)} docker build-push steps"),
    ]

    for name in IMAGE_NAMES:
        matching = [step for step in build_steps if step.get("id") == f"build-{name}"]
        step = matching[0] if matching else {}
        results.extend([
            PolicyResult(f"{name}_image_pushes", _build_step_has(step, "push") and _build_step_has(step, "true"), f"autoops-{name} push=true"),
            PolicyResult(f"{name}_image_immutable_tag", f"steps.image-meta.outputs.{name}" in json.dumps(step), f"autoops-{name} uses computed immutable tag"),
            PolicyResult(f"{name}_image_provenance", _build_step_has(step, "provenance") and _build_step_has(step, "true"), f"autoops-{name} enables provenance"),
            PolicyResult(f"{name}_image_sbom", _build_step_has(step, "sbom") and _build_step_has(step, "true"), f"autoops-{name} enables SBOM attestation"),
            PolicyResult(f"{name}_cosign_signs_digest", f"build-{name}.outputs.digest" in run_text and "cosign sign --yes" in run_text, f"autoops-{name} digest is signed"),
            PolicyResult(f"{name}_cosign_verifies_identity", f"cosign-verify-{name}.json" in run_text and "--certificate-oidc-issuer" in run_text, f"autoops-{name} signature is verified"),
        ])

    results.extend([
        PolicyResult("signature_manifest_written", "reports/image-signatures.json" in run_text, "writes image signing manifest"),
        PolicyResult(
            "signing_artifacts_uploaded",
            "image-signing-evidence" in step_text
            and "reports/image-signatures.json" in step_text
            and "reports/cosign-verify-*.json" in step_text
            and _uses_action(steps, "actions/upload-artifact@v4"),
            "uploads signing and verification evidence",
        ),
    ])

    failed = [result for result in results if not result.ok]
    return {
        "ok": not failed,
        "passed": len(results) - len(failed),
        "failed": len(failed),
        "checks": [asdict(result) for result in results],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check AutoOps image signing policy.")
    parser.add_argument("file", nargs="?", type=pathlib.Path, default=CI_FILE)
    args = parser.parse_args(argv)
    try:
        summary = run_signing_policy_checks(args.file)
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0 if summary["ok"] else 1
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
