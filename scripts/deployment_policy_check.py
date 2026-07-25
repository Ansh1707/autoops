"""Policy checks for Kubernetes deployment proof in CI."""

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
REQUIRED_DEPLOYMENTS = ("redis", "postgres", "api", "worker", "beat", "frontend")
REQUIRED_IMAGES = ("autoops-api:ci", "autoops-worker:ci", "autoops-frontend:ci")


@dataclass(frozen=True)
class PolicyResult:
    check: str
    ok: bool
    detail: str


def _load_workflow(path: pathlib.Path = CI_FILE) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _uses_action(steps: list[dict[str, Any]], action_prefix: str) -> bool:
    return any(str(step.get("uses", "")).startswith(action_prefix) for step in steps)


def _run_text(steps: list[dict[str, Any]]) -> str:
    return "\n".join(str(step.get("run", "")) for step in steps if step.get("run"))


def run_deployment_policy_checks(path: pathlib.Path = CI_FILE) -> dict[str, Any]:
    if not path.exists():
        result = PolicyResult("workflow_exists", False, f"missing workflow: {path}")
        return {"ok": False, "passed": 0, "failed": 1, "checks": [asdict(result)]}

    workflow = _load_workflow(path)
    job = (workflow.get("jobs") or {}).get("k8s-deployment-proof") or {}
    steps = job.get("steps") or []
    permissions = job.get("permissions") or {}
    run_text = _run_text(steps)
    step_text = json.dumps(steps)

    results = [
        PolicyResult("deployment_proof_job_exists", bool(job), "k8s-deployment-proof job is defined"),
        PolicyResult("release_gate_dependency", job.get("needs") == "release-gate", "deployment proof waits for release-gate"),
        PolicyResult("job_timeout", bool(job.get("timeout-minutes")), "deployment proof has timeout-minutes"),
        PolicyResult(
            "read_only_permissions",
            permissions.get("contents") == "read" and len(permissions) == 1,
            json.dumps(permissions, sort_keys=True),
        ),
        PolicyResult("kind_cluster_created", _uses_action(steps, "helm/kind-action@v1"), "creates a Kind cluster"),
        PolicyResult("kubectl_available", _uses_action(steps, "azure/setup-kubectl@v4"), "installs kubectl"),
        PolicyResult(
            "local_images_built",
            all(f"docker build" in run_text and image in run_text for image in REQUIRED_IMAGES),
            ", ".join(REQUIRED_IMAGES),
        ),
        PolicyResult(
            "local_images_loaded",
            all(f"kind load docker-image {image}" in run_text for image in REQUIRED_IMAGES),
            "local images are loaded into Kind",
        ),
        PolicyResult(
            "manifest_rendered",
            "scripts/render_k8s_deployment_proof.py" in run_text and "reports/kind/autoops-kind.yaml" in run_text,
            "Kind manifest is rendered from production template",
        ),
        PolicyResult(
            "secret_created_outside_git",
            "kubectl -n autoops create secret generic autoops-secrets" in run_text
            and "--dry-run=client -o yaml" in run_text,
            "required secret is created dynamically",
        ),
        PolicyResult("manifest_applied", "kubectl apply -f reports/kind/autoops-kind.yaml" in run_text, "manifest is applied"),
        PolicyResult(
            "rollouts_waited",
            all(f"kubectl -n autoops rollout status deployment/{name}" in run_text for name in REQUIRED_DEPLOYMENTS),
            ", ".join(REQUIRED_DEPLOYMENTS),
        ),
        PolicyResult(
            "api_smoke_checked",
            "kubectl -n autoops port-forward svc/api" in run_text
            and "http://127.0.0.1:8000/health" in run_text
            and "http://127.0.0.1:8000/version" in run_text,
            "API health and version endpoints are checked through Kubernetes",
        ),
        PolicyResult(
            "deployment_evidence_uploaded",
            "k8s-deployment-proof" in step_text
            and "reports/kind/" in step_text
            and _uses_action(steps, "actions/upload-artifact@v4"),
            "Kind deployment evidence is uploaded",
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
    parser = argparse.ArgumentParser(description="Check AutoOps Kubernetes deployment proof policy.")
    parser.add_argument("file", nargs="?", type=pathlib.Path, default=CI_FILE)
    args = parser.parse_args(argv)
    try:
        summary = run_deployment_policy_checks(args.file)
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0 if summary["ok"] else 1
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
