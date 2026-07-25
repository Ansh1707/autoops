"""Synthetic resilience checks for AutoOps dependency failure behavior."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import secrets
import sys
import tempfile
import urllib.error
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from typing import Any, Callable, Iterator

import redis
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent import session_memory  # noqa: E402
from api import preflight  # noqa: E402
from api.migrations import ensure_schema  # noqa: E402


@dataclass(frozen=True)
class PolicyResult:
    check: str
    ok: bool
    detail: str


class FailingRedis:
    def rpush(self, *args, **kwargs):
        raise redis.RedisError("synthetic redis outage")

    def expire(self, *args, **kwargs):
        raise redis.RedisError("synthetic redis outage")

    def lrange(self, *args, **kwargs):
        raise redis.RedisError("synthetic redis outage")

    def ping(self):
        raise redis.RedisError("synthetic redis outage")


class FailingDb:
    def execute(self, *args, **kwargs):
        raise RuntimeError("synthetic database outage")


class FakeResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _healthy_urlopen(url: str, timeout: int = 2) -> FakeResponse:
    return FakeResponse()


def _failing_urlopen(url: str, timeout: int = 2):
    raise urllib.error.URLError("synthetic ollama outage")


@contextmanager
def _temporary_env(values: dict[str, str | None]) -> Iterator[None]:
    previous = {key: os.environ.get(key) for key in values}
    try:
        for key, value in values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@contextmanager
def _patched_attribute(target: Any, name: str, value: Any) -> Iterator[None]:
    previous = getattr(target, name)
    try:
        setattr(target, name, value)
        yield
    finally:
        setattr(target, name, previous)


def _db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ensure_schema(engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return engine, Session()


def _base_env(tmp_path: pathlib.Path) -> dict[str, str | None]:
    inbox = tmp_path / "inbox"
    allowed = tmp_path / "allowed"
    chroma = tmp_path / "chroma"
    backups = tmp_path / "backups"
    credentials = tmp_path / "Credentials.json"
    for path in (inbox, allowed, chroma, backups):
        path.mkdir(parents=True, exist_ok=True)
    credentials.write_text("{}", encoding="utf-8")
    return {
        "DATABASE_URL": "sqlite://",
        "REDIS_URL": "redis://localhost:6379/0",
        "OLLAMA_BASE_URL": "http://ollama.local",
        "JWT_SECRET_KEY": secrets.token_hex(32),
        "CHROMA_PERSIST_DIR": str(chroma),
        "AUTOOPS_BACKUP_DIR": str(backups),
        "AUTOOPS_INBOX": str(inbox),
        "AUTOOPS_ALLOWED_ROOTS": str(allowed),
        "GMAIL_CREDENTIALS_PATH": str(credentials),
        "AUTOOPS_RATE_LIMIT_ENABLED": "true",
        "AUTOOPS_REQUIRE_ENCRYPTED_SECRETS": "false",
        "AUTOOPS_BACKUP_ENCRYPT_BY_DEFAULT": "false",
        "AUTOOPS_BACKUP_ENCRYPTION_KEY": None,
    }


def _names(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {check["name"]: check for check in result["checks"]}


def _healthy_preflight_result(tmp_path: pathlib.Path) -> dict[str, Any]:
    engine, db = _db_session()
    try:
        with _temporary_env(_base_env(tmp_path)):
            with _patched_attribute(preflight, "session_memory_status", lambda: {"ok": True, "backend": "redis"}):
                with _patched_attribute(preflight.shutil, "which", lambda name: f"/usr/bin/{name}" if name in {"tesseract", "pdftoppm"} else None):
                    return preflight.run_preflight(db, urlopen=_healthy_urlopen)
    finally:
        db.close()
        engine.dispose()


def _optional_outage_preflight_result(tmp_path: pathlib.Path) -> dict[str, Any]:
    engine, db = _db_session()
    env = _base_env(tmp_path)
    env["GMAIL_CREDENTIALS_PATH"] = str(tmp_path / "missing-Credentials.json")
    try:
        with _temporary_env(env):
            with _patched_attribute(preflight, "session_memory_status", lambda: {"ok": True, "backend": "redis"}):
                with _patched_attribute(preflight.shutil, "which", lambda name: None):
                    return preflight.run_preflight(db, urlopen=_failing_urlopen)
    finally:
        db.close()
        engine.dispose()


def _redis_outage_preflight_result(tmp_path: pathlib.Path) -> dict[str, Any]:
    engine, db = _db_session()
    try:
        with _temporary_env(_base_env(tmp_path)):
            with _patched_attribute(preflight, "session_memory_status", lambda: {"ok": False, "backend": "redis", "error": "synthetic redis outage"}):
                with _patched_attribute(preflight.shutil, "which", lambda name: f"/usr/bin/{name}" if name in {"tesseract", "pdftoppm"} else None):
                    return preflight.run_preflight(db, urlopen=_healthy_urlopen)
    finally:
        db.close()
        engine.dispose()


def _database_outage_preflight_result(tmp_path: pathlib.Path) -> dict[str, Any]:
    with _temporary_env(_base_env(tmp_path)):
        with _patched_attribute(preflight, "session_memory_status", lambda: {"ok": True, "backend": "redis"}):
            with _patched_attribute(preflight.shutil, "which", lambda name: f"/usr/bin/{name}" if name in {"tesseract", "pdftoppm"} else None):
                return preflight.run_preflight(FailingDb(), urlopen=_healthy_urlopen)


def _backup_encryption_required_result(tmp_path: pathlib.Path) -> dict[str, Any]:
    engine, db = _db_session()
    env = _base_env(tmp_path)
    env["AUTOOPS_BACKUP_ENCRYPT_BY_DEFAULT"] = "true"
    env["AUTOOPS_BACKUP_ENCRYPTION_KEY"] = None
    try:
        with _temporary_env(env):
            with _patched_attribute(preflight, "session_memory_status", lambda: {"ok": True, "backend": "redis"}):
                with _patched_attribute(preflight.shutil, "which", lambda name: f"/usr/bin/{name}" if name in {"tesseract", "pdftoppm"} else None):
                    return preflight.run_preflight(db, urlopen=_healthy_urlopen)
    finally:
        db.close()
        engine.dispose()


def _session_memory_degrades() -> tuple[bool, str]:
    with _patched_attribute(session_memory, "redis_client", FailingRedis()):
        try:
            session_memory.save_step("resilience-job", {"event": "synthetic"})
            loaded = session_memory.load_steps("resilience-job")
            status = session_memory.session_memory_status()
        except Exception as exc:
            return False, f"raised {type(exc).__name__}: {exc}"
    ok = loaded == [] and status.get("ok") is False and status.get("backend") == "redis"
    return ok, f"loaded={loaded}; status={status}"


def _safe_result(name: str, fn: Callable[[], tuple[bool, str] | PolicyResult]) -> PolicyResult:
    try:
        result = fn()
        if isinstance(result, PolicyResult):
            return result
        ok, detail = result
        return PolicyResult(name, ok, detail)
    except Exception as exc:
        return PolicyResult(name, False, f"raised {type(exc).__name__}: {exc}")


def run_resilience_policy_checks() -> dict[str, Any]:
    results: list[PolicyResult] = []
    with tempfile.TemporaryDirectory(prefix="autoops-resilience-") as raw_tmp:
        tmp_path = pathlib.Path(raw_tmp)

        healthy = _healthy_preflight_result(tmp_path / "healthy")
        healthy_checks = _names(healthy)
        results.extend([
            PolicyResult("healthy_preflight_passes", healthy["ok"] is True and healthy["required_failed"] == 0, json.dumps({"required_failed": healthy["required_failed"], "optional_failed": healthy["optional_failed"]})),
            PolicyResult("healthy_preflight_covers_required_dependencies", {"database:connection", "redis:session_memory", "path:chroma", "path:backups", "path:inbox"}.issubset(healthy_checks), ",".join(sorted(healthy_checks))),
            PolicyResult("healthy_preflight_covers_optional_dependencies", {"gmail:credentials", "ocr:tooling", "ollama:api"}.issubset(healthy_checks), ",".join(sorted(healthy_checks))),
        ])

        optional_outage = _optional_outage_preflight_result(tmp_path / "optional")
        optional_checks = _names(optional_outage)
        optional_failed_names = {check["name"] for check in optional_outage["checks"] if not check["ok"] and not check["required"]}
        results.extend([
            PolicyResult("optional_outages_do_not_block_readiness", optional_outage["ok"] is True and optional_outage["required_failed"] == 0, json.dumps({"required_failed": optional_outage["required_failed"], "optional_failed": optional_outage["optional_failed"]})),
            PolicyResult("gmail_outage_is_optional", optional_checks["gmail:credentials"]["ok"] is False and optional_checks["gmail:credentials"]["required"] is False, optional_checks["gmail:credentials"]["detail"]),
            PolicyResult("ocr_outage_is_optional", optional_checks["ocr:tooling"]["ok"] is False and optional_checks["ocr:tooling"]["required"] is False, optional_checks["ocr:tooling"]["detail"]),
            PolicyResult("ollama_outage_is_optional", optional_checks["ollama:api"]["ok"] is False and optional_checks["ollama:api"]["required"] is False, optional_checks["ollama:api"]["detail"]),
            PolicyResult("optional_outage_count_is_reported", {"gmail:credentials", "ocr:tooling", "ollama:api"}.issubset(optional_failed_names), ",".join(sorted(optional_failed_names))),
        ])

        redis_outage = _redis_outage_preflight_result(tmp_path / "redis")
        redis_check = _names(redis_outage)["redis:session_memory"]
        results.extend([
            PolicyResult("redis_outage_blocks_required_readiness", redis_outage["ok"] is False and redis_outage["required_failed"] >= 1, json.dumps({"required_failed": redis_outage["required_failed"]})),
            PolicyResult("redis_outage_has_actionable_detail", redis_check["required"] is True and "synthetic redis outage" in redis_check["detail"], redis_check["detail"]),
        ])

        database_outage = _database_outage_preflight_result(tmp_path / "database")
        database_check = _names(database_outage)["database:connection"]
        results.extend([
            PolicyResult("database_outage_blocks_required_readiness", database_outage["ok"] is False and database_outage["required_failed"] >= 1, json.dumps({"required_failed": database_outage["required_failed"]})),
            PolicyResult("database_outage_has_actionable_detail", database_check["required"] is True and "synthetic database outage" in database_check["detail"], database_check["detail"]),
        ])

        encryption_required = _backup_encryption_required_result(tmp_path / "backup-encryption")
        encryption_check = _names(encryption_required)["backup:encryption"]
        results.extend([
            PolicyResult("backup_encryption_required_blocks_without_key", encryption_required["ok"] is False and encryption_check["required"] is True and encryption_check["ok"] is False, encryption_check["detail"]),
            PolicyResult("backup_encryption_failure_names_missing_key", "AUTOOPS_BACKUP_ENCRYPTION_KEY" in encryption_check["detail"], encryption_check["detail"]),
        ])

        results.append(_safe_result("session_memory_degrades_when_redis_fails", _session_memory_degrades))

    failed = [result for result in results if not result.ok]
    return {
        "ok": not failed,
        "passed": len(results) - len(failed),
        "failed": len(failed),
        "checks": [asdict(result) for result in results],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check AutoOps resilience and graceful-degradation behavior.")
    parser.parse_args(argv)
    summary = run_resilience_policy_checks()
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
