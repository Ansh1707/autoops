from __future__ import annotations

import os
import pathlib
import shutil
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from typing import Callable

from sqlalchemy import text
from sqlalchemy.orm import Session

from agent.session_memory import session_memory_status
from api.backups import backup_encryption_status
from api.rate_limit import rate_limit_config
from api.secrets import encrypted_secrets_status


@dataclass(frozen=True)
class PreflightCheck:
    name: str
    ok: bool
    required: bool
    detail: str


def _check_env(name: str, required: bool = True) -> PreflightCheck:
    value = os.getenv(name, "").strip()
    return PreflightCheck(
        name=f"env:{name}",
        ok=bool(value),
        required=required,
        detail="set" if value else "missing",
    )


def _check_path(path: pathlib.Path, name: str, required: bool, must_be_writable: bool = False) -> PreflightCheck:
    exists = path.exists()
    if not exists:
        return PreflightCheck(name=name, ok=False, required=required, detail=f"missing: {path}")
    if must_be_writable and not os.access(path, os.W_OK):
        return PreflightCheck(name=name, ok=False, required=required, detail=f"not writable: {path}")
    if not os.access(path, os.R_OK):
        return PreflightCheck(name=name, ok=False, required=required, detail=f"not readable: {path}")
    return PreflightCheck(name=name, ok=True, required=required, detail=str(path))


def _check_allowed_roots() -> list[PreflightCheck]:
    raw = os.getenv("AUTOOPS_ALLOWED_ROOTS", os.getcwd())
    checks: list[PreflightCheck] = []
    for index, root in enumerate(part for part in raw.split(os.pathsep) if part.strip()):
        checks.append(
            _check_path(
                pathlib.Path(root).expanduser(),
                name=f"path:allowed_root:{index}",
                required=True,
            )
        )
    if not checks:
        checks.append(PreflightCheck("path:allowed_root", False, True, "no roots configured"))
    return checks


def _check_chroma_path() -> PreflightCheck:
    path = pathlib.Path(os.getenv("CHROMA_PERSIST_DIR", "/root/.cache/chroma_data")).expanduser()
    try:
        path.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        return PreflightCheck("path:chroma", False, True, f"cannot create {path}: {exc}")
    return _check_path(path, "path:chroma", required=True, must_be_writable=True)


def _check_backup_path() -> PreflightCheck:
    path = pathlib.Path(os.getenv("AUTOOPS_BACKUP_DIR", "backups")).expanduser()
    try:
        path.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        return PreflightCheck("path:backups", False, True, f"cannot create {path}: {exc}")
    return _check_path(path, "path:backups", required=True, must_be_writable=True)


def _check_backup_encryption() -> PreflightCheck:
    status = backup_encryption_status()
    required = bool(status["encrypt_by_default"])
    ok = bool(status["valid"]) if required else bool(status["valid"] or not status["configured"])
    return PreflightCheck(
        "backup:encryption",
        ok,
        required,
        status["detail"],
    )


def _check_encrypted_secrets() -> PreflightCheck:
    status = encrypted_secrets_status()
    ok = bool(status.valid)
    return PreflightCheck(
        "secrets:encrypted_file",
        ok,
        status.required,
        status.detail if not status.configured else f"{status.detail}; path={status.path}",
    )


def _check_rate_limit_config() -> PreflightCheck:
    config = rate_limit_config()
    if not config["enabled"]:
        return PreflightCheck("rate_limit:config", False, False, "rate limiting disabled")
    return PreflightCheck(
        "rate_limit:config",
        True,
        False,
        (
            f"global={config['global_requests_per_minute']}/min; "
            f"job_submit={config['job_submits_per_minute']}/min; "
            f"window={config['window_seconds']}s"
        ),
    )


def _check_gmail_credentials() -> PreflightCheck:
    configured = os.getenv("GMAIL_CREDENTIALS_PATH")
    if configured:
        path = pathlib.Path(configured).expanduser()
        if path.exists():
            return PreflightCheck("gmail:credentials", True, False, str(path))
        return PreflightCheck("gmail:credentials", False, False, f"configured path missing: {path}")

    candidates = [
        pathlib.Path("credentials.json"),
        pathlib.Path("Credentials.json"),
    ]
    for candidate in candidates:
        if candidate and candidate.exists():
            return PreflightCheck("gmail:credentials", True, False, str(candidate))
    return PreflightCheck("gmail:credentials", False, False, "not configured")


def _check_ollama(urlopen: Callable = urllib.request.urlopen) -> PreflightCheck:
    base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
    try:
        with urlopen(f"{base_url}/api/tags", timeout=2) as response:
            ok = 200 <= getattr(response, "status", 200) < 500
        return PreflightCheck("ollama:api", ok, False, base_url)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return PreflightCheck("ollama:api", False, False, f"{base_url}: {exc}")


def _check_ocr_tooling() -> PreflightCheck:
    tesseract = shutil.which("tesseract")
    pdftoppm = shutil.which("pdftoppm")
    if tesseract and pdftoppm:
        return PreflightCheck("ocr:tooling", True, False, f"tesseract={tesseract}; pdftoppm={pdftoppm}")
    missing = []
    if not tesseract:
        missing.append("tesseract")
    if not pdftoppm:
        missing.append("pdftoppm")
    return PreflightCheck("ocr:tooling", False, False, f"missing optional tools: {', '.join(missing)}")


def _check_database(db: Session) -> PreflightCheck:
    try:
        db.execute(text("SELECT 1"))
        return PreflightCheck("database:connection", True, True, "ok")
    except Exception as exc:
        return PreflightCheck("database:connection", False, True, str(exc))


def _check_redis() -> PreflightCheck:
    status = session_memory_status()
    return PreflightCheck(
        "redis:session_memory",
        bool(status.get("ok")),
        True,
        status.get("error") or "ok",
    )


def run_preflight(db: Session, urlopen: Callable = urllib.request.urlopen) -> dict:
    checks = [
        _check_env("DATABASE_URL"),
        _check_env("REDIS_URL"),
        _check_env("OLLAMA_BASE_URL", required=False),
        _check_encrypted_secrets(),
        _check_env("JWT_SECRET_KEY"),
        _check_database(db),
        _check_redis(),
        _check_chroma_path(),
        _check_backup_path(),
        _check_backup_encryption(),
        _check_rate_limit_config(),
        _check_path(
            pathlib.Path(os.getenv("AUTOOPS_INBOX", "inbox")).expanduser(),
            name="path:inbox",
            required=True,
            must_be_writable=True,
        ),
        *_check_allowed_roots(),
        _check_gmail_credentials(),
        _check_ocr_tooling(),
        _check_ollama(urlopen=urlopen),
    ]
    required_failed = [check for check in checks if check.required and not check.ok]
    optional_failed = [check for check in checks if not check.required and not check.ok]
    return {
        "ok": not required_failed,
        "required_failed": len(required_failed),
        "optional_failed": len(optional_failed),
        "checks": [asdict(check) for check in checks],
    }
