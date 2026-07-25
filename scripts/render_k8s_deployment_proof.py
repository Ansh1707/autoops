"""Render a Kind-friendly deployment proof manifest from the production template."""

from __future__ import annotations

import argparse
import pathlib
from typing import Any

import yaml


ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "k8s" / "autoops-cluster.yaml"
IMAGE_OVERRIDES = {
    ("Deployment", "api", "api"): "autoops-api:ci",
    ("Deployment", "worker", "worker"): "autoops-worker:ci",
    ("Deployment", "beat", "beat"): "autoops-worker:ci",
    ("Deployment", "frontend", "frontend"): "autoops-frontend:ci",
}
PROOF_RESOURCE_REQUESTS = {
    "cpu": "50m",
    "memory": "128Mi",
}


def _metadata(resource: dict[str, Any]) -> dict[str, Any]:
    return resource.get("metadata") or {}


def _containers(resource: dict[str, Any]) -> list[dict[str, Any]]:
    return list(
        (((resource.get("spec") or {}).get("template") or {}).get("spec") or {}).get("containers") or []
    )


def render_resources(source: pathlib.Path = DEFAULT_SOURCE) -> list[dict[str, Any]]:
    resources = [resource for resource in yaml.safe_load_all(source.read_text(encoding="utf-8")) if resource]
    for resource in resources:
        kind = resource.get("kind")
        name = _metadata(resource).get("name")
        if kind == "ConfigMap" and name == "autoops-config":
            data = resource.setdefault("data", {})
            data["AUTOOPS_ENV"] = "production"
            data["OLLAMA_BASE_URL"] = "http://ollama:11434"
        if kind == "PersistentVolumeClaim":
            storage = (((resource.get("spec") or {}).get("resources") or {}).get("requests") or {})
            storage["storage"] = "1Gi"
        if kind == "Deployment":
            resource.setdefault("spec", {})["replicas"] = 1
            for container in _containers(resource):
                key = (kind, name, container.get("name"))
                if key in IMAGE_OVERRIDES:
                    container["image"] = IMAGE_OVERRIDES[key]
                    container["imagePullPolicy"] = "Never"
                container_resources = container.setdefault("resources", {})
                container_resources["requests"] = dict(PROOF_RESOURCE_REQUESTS)
    return resources


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render AutoOps Kubernetes manifests for Kind deployment proof.")
    parser.add_argument("--source", type=pathlib.Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args(argv)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(yaml.safe_dump_all(render_resources(args.source), sort_keys=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
