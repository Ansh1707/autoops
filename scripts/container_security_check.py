"""Static container hardening checks for AutoOps.

The goal is not to replace a vulnerability scanner. This enforces the local
baseline that should never regress: pinned image tags, non-root users, clean apt
usage, deterministic npm installs, and no obvious secret copies into images.
"""

from __future__ import annotations

import json
import pathlib
import re
import sys
from dataclasses import asdict, dataclass


ROOT = pathlib.Path(__file__).resolve().parents[1]
DOCKERFILES = [
    ROOT / "Dockerfile.api",
    ROOT / "Dockerfile.worker",
    ROOT / "frontend" / "Dockerfile.frontend",
]
COMPOSE_FILE = ROOT / "docker-compose.yml"
SENSITIVE_COPY_PATTERN = re.compile(
    r"\bCOPY\b.*(?:\.env|credentials\.json|Credentials\.json|token\.json|gmail_token\.json|secrets\.autoops\.enc)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PolicyResult:
    file: str
    check: str
    ok: bool
    detail: str


def _relative(path: pathlib.Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _from_images(lines: list[str]) -> list[str]:
    return [line.split()[1] for line in lines if line.strip().upper().startswith("FROM ")]


def _has_non_root_user(lines: list[str]) -> bool:
    for line in lines:
        stripped = line.strip()
        if not stripped.upper().startswith("USER "):
            continue
        user = stripped.split(maxsplit=1)[1]
        return user not in {"0", "root"}
    return False


def _base_images_are_pinned(images: list[str]) -> tuple[bool, str]:
    for image in images:
        reference = image.split("@", 1)[0]
        if ":" not in reference:
            return False, f"base image is missing an explicit tag: {image}"
        tag = reference.rsplit(":", 1)[1]
        if tag in {"latest", "slim", "alpine"}:
            return False, f"base image tag is too broad: {image}"
        if not re.search(r"\d+\.\d+\.\d+", tag):
            return False, f"base image tag must include a patch version: {image}"
    return True, ", ".join(images)


def _apt_usage_is_hardened(text: str) -> tuple[bool, str]:
    if "apt-get install" not in text:
        return True, "no apt usage"
    required = ["--no-install-recommends", "rm -rf /var/lib/apt/lists/*"]
    missing = [item for item in required if item not in text]
    if missing:
        return False, "missing " + ", ".join(missing)
    return True, "apt install uses no-install-recommends and cleans package lists"


def _uses_deterministic_npm(path: pathlib.Path, text: str) -> tuple[bool, str]:
    if path.name != "Dockerfile.frontend":
        return True, "not a Node image"
    if "npm ci" not in text:
        return False, "frontend image must use npm ci"
    if "npm install" in text:
        return False, "frontend image should not use npm install"
    return True, "frontend image uses npm ci"


def check_dockerfile(path: pathlib.Path) -> list[PolicyResult]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    rel = _relative(path)
    images = _from_images(lines)
    pinned_ok, pinned_detail = _base_images_are_pinned(images)
    apt_ok, apt_detail = _apt_usage_is_hardened(text)
    npm_ok, npm_detail = _uses_deterministic_npm(path, text)
    sensitive_copy = SENSITIVE_COPY_PATTERN.search(text)

    return [
        PolicyResult(rel, "base_image_pinned", pinned_ok, pinned_detail),
        PolicyResult(rel, "non_root_user", _has_non_root_user(lines), "Dockerfile declares a non-root USER"),
        PolicyResult(rel, "apt_hygiene", apt_ok, apt_detail),
        PolicyResult(rel, "deterministic_npm", npm_ok, npm_detail),
        PolicyResult(
            rel,
            "no_secret_copy",
            sensitive_copy is None,
            "no direct COPY of local secrets or OAuth files",
        ),
    ]


def check_compose(path: pathlib.Path = COMPOSE_FILE) -> list[PolicyResult]:
    text = path.read_text(encoding="utf-8")
    rel = _relative(path)
    cache_is_non_root = "/root/.cache" not in text and "/app/.cache" in text
    chroma_env_count = text.count("CHROMA_PERSIST_DIR=/app/.cache/chroma_data")
    literal_postgres_password = re.search(r"POSTGRES_PASSWORD\s*=\s*(?!\$\{)[^\s]+", text)
    database_url_lines = [
        line.strip()
        for line in text.splitlines()
        if "DATABASE_URL=postgresql://" in line
    ]
    database_passwords_from_environment = bool(database_url_lines) and all(
        "${POSTGRES_PASSWORD}" in line for line in database_url_lines
    )
    runtime_secrets_required = (
        text.count("JWT_SECRET_KEY=${JWT_SECRET_KEY:?") >= 3
        and text.count("AUTOOPS_BOOTSTRAP_PASSWORD=${AUTOOPS_BOOTSTRAP_PASSWORD:?") >= 3
        and "POSTGRES_PASSWORD=${POSTGRES_PASSWORD:?" in text
    )
    postgres_data_persistent = "postgres_data:/var/lib/postgresql/data" in text
    return [
        PolicyResult(
            rel,
            "non_root_cache_path",
            cache_is_non_root,
            "Chroma cache volume targets /app/.cache instead of /root/.cache",
        ),
        PolicyResult(
            rel,
            "chroma_persist_dir_configured",
            chroma_env_count >= 3,
            "API, worker, and beat configure CHROMA_PERSIST_DIR under /app/.cache",
        ),
        PolicyResult(
            rel,
            "no_hardcoded_database_credentials",
            literal_postgres_password is None and database_passwords_from_environment,
            "PostgreSQL passwords are supplied through environment interpolation",
        ),
        PolicyResult(
            rel,
            "runtime_secrets_required",
            runtime_secrets_required,
            "Compose requires PostgreSQL, JWT, and bootstrap secrets from the local environment",
        ),
        PolicyResult(
            rel,
            "portable_host_mounts",
            "/Users/" not in text,
            "Host mounts use portable environment references instead of personal absolute paths",
        ),
        PolicyResult(
            rel,
            "postgres_data_persistent",
            postgres_data_persistent,
            "PostgreSQL data is stored in a named volume across container recreation",
        ),
    ]


def run_container_security_checks() -> dict:
    results: list[PolicyResult] = []
    for path in DOCKERFILES:
        if not path.exists():
            results.append(PolicyResult(_relative(path), "exists", False, "Dockerfile is missing"))
            continue
        results.extend(check_dockerfile(path))
    if COMPOSE_FILE.exists():
        results.extend(check_compose(COMPOSE_FILE))
    else:
        results.append(PolicyResult(_relative(COMPOSE_FILE), "exists", False, "docker-compose.yml is missing"))

    failed = [result for result in results if not result.ok]
    return {
        "ok": not failed,
        "passed": len(results) - len(failed),
        "failed": len(failed),
        "checks": [asdict(result) for result in results],
    }


def main() -> int:
    summary = run_container_security_checks()
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
