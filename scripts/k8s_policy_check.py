"""Static Kubernetes manifest policy checks for AutoOps."""

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
K8S_DIR = ROOT / "k8s"
K8S_FILES = [K8S_DIR / "autoops-cluster.yaml"]
WORKLOAD_KINDS = {"Deployment", "StatefulSet", "DaemonSet"}
SECRET_LIKE_KEYS = {"password", "secret", "token", "credential", "key"}


@dataclass(frozen=True)
class PolicyResult:
    resource: str
    check: str
    ok: bool
    detail: str


def _resource_name(resource: dict[str, Any]) -> str:
    metadata = resource.get("metadata") or {}
    return f"{resource.get('kind', 'Unknown')}/{metadata.get('name', 'unnamed')}"


def load_manifests(paths: list[pathlib.Path] | None = None) -> list[dict[str, Any]]:
    resources: list[dict[str, Any]] = []
    for path in paths or K8S_FILES:
        for item in yaml.safe_load_all(path.read_text(encoding="utf-8")):
            if item:
                resources.append(item)
    return resources


def _image_is_pinned(image: str) -> bool:
    if "@sha256:" in image:
        return True
    if ":" not in image:
        return False
    tag = image.rsplit(":", 1)[1]
    return tag not in {"latest", "stable", "alpine", "slim"} and bool(re.search(r"\d", tag))


def _containers(resource: dict[str, Any]) -> list[dict[str, Any]]:
    spec = resource.get("spec") or {}
    template = spec.get("template") or {}
    pod_spec = template.get("spec") or {}
    return list(pod_spec.get("containers") or [])


def _pod_spec(resource: dict[str, Any]) -> dict[str, Any]:
    return (((resource.get("spec") or {}).get("template") or {}).get("spec") or {})


def _has_secret_literal(resource: dict[str, Any]) -> bool:
    if resource.get("kind") == "Secret":
        return True
    text = json.dumps(resource).lower()
    return any(f'"{key}": "postgres"' in text for key in SECRET_LIKE_KEYS)


def check_workload(resource: dict[str, Any]) -> list[PolicyResult]:
    name = _resource_name(resource)
    pod_spec = _pod_spec(resource)
    pod_security = pod_spec.get("securityContext") or {}
    containers = _containers(resource)
    results = [
        PolicyResult(name, "service_account_token_disabled", pod_spec.get("automountServiceAccountToken") is False, "automountServiceAccountToken=false"),
        PolicyResult(name, "pod_runs_non_root", pod_security.get("runAsNonRoot") is True, "pod securityContext.runAsNonRoot=true"),
        PolicyResult(
            name,
            "seccomp_runtime_default",
            (pod_security.get("seccompProfile") or {}).get("type") == "RuntimeDefault",
            "pod seccompProfile.type=RuntimeDefault",
        ),
    ]

    for container in containers:
        container_name = container.get("name", "container")
        prefix = f"{name}:{container_name}"
        security = container.get("securityContext") or {}
        capabilities = security.get("capabilities") or {}
        resources = container.get("resources") or {}
        requests = resources.get("requests") or {}
        limits = resources.get("limits") or {}
        mounts = container.get("volumeMounts") or []
        mount_paths = {mount.get("mountPath") for mount in mounts}
        image = container.get("image", "")

        results.extend([
            PolicyResult(prefix, "image_pinned", _image_is_pinned(image), image or "image missing"),
            PolicyResult(prefix, "resources_set", bool(requests.get("cpu") and requests.get("memory") and limits.get("cpu") and limits.get("memory")), "cpu/memory requests and limits set"),
            PolicyResult(prefix, "liveness_probe", "livenessProbe" in container, "livenessProbe configured"),
            PolicyResult(prefix, "readiness_probe", "readinessProbe" in container, "readinessProbe configured"),
            PolicyResult(prefix, "no_privilege_escalation", security.get("allowPrivilegeEscalation") is False, "allowPrivilegeEscalation=false"),
            PolicyResult(prefix, "drops_linux_capabilities", "ALL" in capabilities.get("drop", []), "drops ALL Linux capabilities"),
            PolicyResult(prefix, "no_root_cache_mount", "/root/.cache" not in mount_paths, "does not mount /root/.cache"),
        ])

        if container_name in {"api", "worker", "beat", "frontend"}:
            results.append(
                PolicyResult(
                    prefix,
                    "readonly_root_filesystem",
                    security.get("readOnlyRootFilesystem") is True,
                    "app containers use readOnlyRootFilesystem=true",
                )
            )

    return results


def run_k8s_policy_checks(paths: list[pathlib.Path] | None = None) -> dict:
    resources = load_manifests(paths)
    results: list[PolicyResult] = []
    kinds = {resource.get("kind") for resource in resources}
    names = {_resource_name(resource) for resource in resources}

    for required in ["Namespace/autoops", "NetworkPolicy/autoops-default-deny", "NetworkPolicy/autoops-allow-required-traffic"]:
        results.append(PolicyResult(required, "required_resource_present", required in names, f"{required} exists"))

    results.append(PolicyResult("cluster", "no_committed_secret_resources", "Secret" not in kinds, "manifests reference externally-created Kubernetes Secrets"))

    for resource in resources:
        name = _resource_name(resource)
        results.append(PolicyResult(name, "no_secret_literals", not _has_secret_literal(resource), "no committed secret literal material"))
        if resource.get("kind") == "Service" and (resource.get("metadata") or {}).get("name") == "api":
            annotations = (resource.get("metadata") or {}).get("annotations") or {}
            results.append(
                PolicyResult(
                    name,
                    "prometheus_scrape_annotation",
                    annotations.get("prometheus.io/scrape") == "true"
                    and annotations.get("prometheus.io/path") == "/metrics/prometheus",
                    "api Service advertises /metrics/prometheus scrape path",
                )
            )
        if resource.get("kind") in WORKLOAD_KINDS:
            results.extend(check_workload(resource))

    failed = [result for result in results if not result.ok]
    return {
        "ok": not failed,
        "passed": len(results) - len(failed),
        "failed": len(failed),
        "resources": len(resources),
        "checks": [asdict(result) for result in results],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run static policy checks for AutoOps Kubernetes manifests.")
    parser.add_argument("files", nargs="*", type=pathlib.Path, help="Manifest files to check. Defaults to k8s/autoops-cluster.yaml.")
    args = parser.parse_args(argv)
    try:
        summary = run_k8s_policy_checks(args.files or None)
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0 if summary["ok"] else 1
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
