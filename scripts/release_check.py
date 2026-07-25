from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import time
from dataclasses import asdict, dataclass


ROOT = pathlib.Path(__file__).resolve().parents[1]
PYTHON = sys.executable
OUTPUT_LIMIT = 4000


@dataclass
class CheckResult:
    name: str
    ok: bool
    duration_ms: int
    command: list[str]
    returncode: int
    output: str


def python_files() -> list[str]:
    ignored_parts = {"venv", "__pycache__", ".pytest_cache", "node_modules", ".git"}
    files = []
    for path in ROOT.rglob("*.py"):
        relative = path.relative_to(ROOT)
        if any(part in ignored_parts for part in relative.parts):
            continue
        files.append(str(relative))
    return sorted(files)


def run_command(name: str, command: list[str], cwd: pathlib.Path = ROOT, env: dict | None = None) -> CheckResult:
    started = time.monotonic()
    merged_env = os.environ.copy()
    merged_env["AUTOOPS_LOAD_DOTENV"] = "false"
    merged_env.setdefault("CHROMA_PERSIST_DIR", "/tmp/autoops-release-chroma")
    merged_env.setdefault("JWT_SECRET_KEY", "release-check-secret-with-at-least-32-bytes")
    if name == "docker_compose_config":
        merged_env.setdefault("POSTGRES_PASSWORD", "release-check-postgres-password")
        merged_env.setdefault("AUTOOPS_BOOTSTRAP_PASSWORD", "release-check-bootstrap-password")
    if env:
        merged_env.update(env)

    completed = subprocess.run(
        command,
        cwd=cwd,
        env=merged_env,
        capture_output=True,
        text=True,
        check=False,
    )
    output = (completed.stdout + completed.stderr).strip()
    if len(output) > OUTPUT_LIMIT:
        output = output[-OUTPUT_LIMIT:]
    return CheckResult(
        name=name,
        ok=completed.returncode == 0,
        duration_ms=int((time.monotonic() - started) * 1000),
        command=command,
        returncode=completed.returncode,
        output=output,
    )


def build_checks(skip_frontend: bool = False, skip_docker: bool = False) -> list[tuple[str, list[str], pathlib.Path]]:
    checks: list[tuple[str, list[str], pathlib.Path]] = [
        ("python_compile", [PYTHON, "-m", "py_compile", *python_files()], ROOT),
        ("pytest", [PYTHON, "-m", "pytest", "-v"], ROOT),
        ("agent_evals", [PYTHON, "scripts/run_evals.py"], ROOT),
        ("container_security", [PYTHON, "scripts/container_security_check.py"], ROOT),
        ("dependency_policy", [PYTHON, "scripts/dependency_policy_check.py"], ROOT),
        ("secret_scanning_policy", [PYTHON, "scripts/secret_scanning_policy_check.py"], ROOT),
        ("publication_policy", [PYTHON, "scripts/publication_policy_check.py"], ROOT),
        ("ci_policy", [PYTHON, "scripts/ci_policy_check.py"], ROOT),
        ("vulnerability_policy", [PYTHON, "scripts/vulnerability_policy_check.py"], ROOT),
        ("signing_policy", [PYTHON, "scripts/signing_policy_check.py"], ROOT),
        ("deployment_policy", [PYTHON, "scripts/deployment_policy_check.py"], ROOT),
        ("frontend_dashboard_policy", [PYTHON, "scripts/frontend_dashboard_policy_check.py"], ROOT),
        ("api_contract_policy", [PYTHON, "scripts/api_contract_policy_check.py"], ROOT),
        ("performance_policy", [PYTHON, "scripts/performance_policy_check.py"], ROOT),
        ("migration_policy", [PYTHON, "scripts/migration_policy_check.py"], ROOT),
        ("resilience_policy", [PYTHON, "scripts/resilience_policy_check.py"], ROOT),
        ("k8s_policy", [PYTHON, "scripts/k8s_policy_check.py"], ROOT),
        ("alert_policy", [PYTHON, "scripts/alert_policy_check.py"], ROOT),
        ("dr_drill", [PYTHON, "scripts/dr_drill.py"], ROOT),
        ("release_manifest", [PYTHON, "scripts/release_manifest.py"], ROOT),
    ]
    if not skip_docker:
        checks.append(("docker_compose_config", ["docker", "compose", "config", "--quiet"], ROOT))
    if not skip_frontend:
        checks.extend([
            ("frontend_lint", ["npm", "run", "lint"], ROOT / "frontend"),
            ("frontend_build", ["npm", "run", "build"], ROOT / "frontend"),
        ])
    return checks


def run_release_checks(skip_frontend: bool = False, skip_docker: bool = False) -> dict:
    results = [
        run_command(name, command, cwd)
        for name, command, cwd in build_checks(skip_frontend=skip_frontend, skip_docker=skip_docker)
    ]
    return {
        "ok": all(result.ok for result in results),
        "passed": sum(1 for result in results if result.ok),
        "failed": sum(1 for result in results if not result.ok),
        "checks": [asdict(result) for result in results],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the AutoOps release readiness gate.")
    parser.add_argument("--skip-frontend", action="store_true", help="Skip frontend lint/build checks.")
    parser.add_argument("--skip-docker", action="store_true", help="Skip docker compose config validation.")
    args = parser.parse_args()

    summary = run_release_checks(skip_frontend=args.skip_frontend, skip_docker=args.skip_docker)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
