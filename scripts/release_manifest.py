from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
from datetime import UTC, datetime
from typing import Any


ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.version import build_metadata, read_version  # noqa: E402
from scripts import alert_policy_check, api_contract_policy_check, container_security_check, dependency_policy_check, deployment_policy_check, frontend_dashboard_policy_check, k8s_policy_check, migration_policy_check, performance_policy_check, publication_policy_check, release_check, resilience_policy_check, secret_scanning_policy_check, signing_policy_check, vulnerability_policy_check  # noqa: E402


SOURCE_ARTIFACTS = [
    "VERSION",
    "CHANGELOG.md",
    "requirements.txt",
    "Dockerfile.api",
    "Dockerfile.worker",
    "docker-compose.yml",
    "frontend/Dockerfile.frontend",
    "frontend/package.json",
    "frontend/package-lock.json",
    "frontend/src/App.tsx",
    "frontend/src/index.css",
    "docs/api/openapi.json",
    "db/migrations/manifest.json",
    "db/migrations/0001_initial_jobs.sql",
    "db/migrations/0002_audit_chain.sql",
    "db/migrations/0003_users_rbac.sql",
    "db/migrations/0004_schema_ledger.sql",
    ".github/workflows/ci.yml",
    ".pre-commit-config.yaml",
    "requirements-dev.txt",
    "k8s/autoops-cluster.yaml",
    "k8s/prometheus-alerts.yaml",
    "scripts/release_check.py",
    "scripts/release_manifest.py",
    "scripts/render_k8s_deployment_proof.py",
    "scripts/deployment_policy_check.py",
    "scripts/frontend_dashboard_policy_check.py",
    "scripts/api_contract_policy_check.py",
    "scripts/performance_policy_check.py",
    "scripts/migration_policy_check.py",
    "scripts/resilience_policy_check.py",
    "scripts/secret_scanning_policy_check.py",
    "scripts/publication_policy_check.py",
]


def _sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_hashes(paths: list[str] = SOURCE_ARTIFACTS) -> list[dict[str, Any]]:
    artifacts = []
    for relative in paths:
        path = ROOT / relative
        artifacts.append(
            {
                "path": relative,
                "exists": path.exists(),
                "sha256": _sha256(path) if path.exists() else None,
                "bytes": path.stat().st_size if path.exists() else 0,
            }
        )
    return artifacts


def _policy_summary(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": bool(summary.get("ok")),
        "passed": int(summary.get("passed", 0)),
        "failed": int(summary.get("failed", 0)),
    }


def build_release_manifest() -> dict[str, Any]:
    dependency_summary = dependency_policy_check.run_dependency_policy_checks()
    policies = {
        "container_security": _policy_summary(container_security_check.run_container_security_checks()),
        "dependency_policy": {
            **_policy_summary(dependency_summary),
            "sbom_summary": dependency_summary.get("sbom_summary", {}),
        },
        "secret_scanning_policy": _policy_summary(
            secret_scanning_policy_check.run_secret_scanning_policy_checks()
        ),
        "publication_policy": _policy_summary(
            publication_policy_check.run_publication_policy_checks()
        ),
        "vulnerability_policy": _policy_summary(vulnerability_policy_check.run_vulnerability_policy_checks()),
        "signing_policy": _policy_summary(signing_policy_check.run_signing_policy_checks()),
        "deployment_policy": _policy_summary(deployment_policy_check.run_deployment_policy_checks()),
        "frontend_dashboard_policy": _policy_summary(frontend_dashboard_policy_check.run_frontend_dashboard_policy_checks()),
        "api_contract_policy": _policy_summary(api_contract_policy_check.run_api_contract_policy_checks()),
        "performance_policy": _policy_summary(performance_policy_check.run_performance_policy_checks()),
        "migration_policy": _policy_summary(migration_policy_check.run_migration_policy_checks()),
        "resilience_policy": _policy_summary(resilience_policy_check.run_resilience_policy_checks()),
        "k8s_policy": _policy_summary(k8s_policy_check.run_k8s_policy_checks()),
        "alert_policy": _policy_summary(alert_policy_check.run_alert_policy_checks()),
    }
    artifacts = source_hashes()
    release_gate_checks = [name for name, _command, _cwd in release_check.build_checks()]
    missing_artifacts = [artifact["path"] for artifact in artifacts if not artifact["exists"]]

    return {
        "schema_version": "1.0",
        "project": "autoops",
        "version": read_version(),
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "runtime": build_metadata(),
        "source_artifacts": artifacts,
        "policy_summaries": policies,
        "release_gate_checks": release_gate_checks,
        "ok": not missing_artifacts and all(policy["ok"] for policy in policies.values()),
        "missing_artifacts": missing_artifacts,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate an AutoOps release provenance manifest.")
    parser.add_argument("--output", type=pathlib.Path, help="Optional path to write the release manifest JSON.")
    args = parser.parse_args(argv)

    manifest = build_release_manifest()
    payload = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0 if manifest["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
