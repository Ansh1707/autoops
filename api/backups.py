from __future__ import annotations

import hashlib
import io
import json
import os
import pathlib
import tempfile
import zipfile
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from api.models import AuditEvent, InvestigationJob


BACKUP_VERSION = "2026-07-05.1"
ENCRYPTION_SCHEME = "fernet-v1"
EXCLUDED_NAMES = {
    ".DS_Store",
    "__pycache__",
    ".pytest_cache",
}


def backup_root() -> pathlib.Path:
    return pathlib.Path(os.getenv("AUTOOPS_BACKUP_DIR", "backups")).expanduser().resolve()


def ensure_backup_root() -> pathlib.Path:
    root = backup_root()
    root.mkdir(parents=True, exist_ok=True)
    return root


def backup_id_for_now(now: datetime | None = None) -> str:
    stamp = (now or datetime.utcnow()).strftime("%Y%m%dT%H%M%SZ")
    return f"autoops-backup-{stamp}.zip"


def encryption_enabled_by_default() -> bool:
    return os.getenv("AUTOOPS_BACKUP_ENCRYPT_BY_DEFAULT", "false").lower() == "true"


def encryption_key_configured() -> bool:
    return bool(os.getenv("AUTOOPS_BACKUP_ENCRYPTION_KEY", "").strip())


def backup_encryption_status() -> dict:
    try:
        if not encryption_key_configured():
            return {
                "configured": False,
                "valid": False,
                "encrypt_by_default": encryption_enabled_by_default(),
                "scheme": ENCRYPTION_SCHEME,
                "detail": "AUTOOPS_BACKUP_ENCRYPTION_KEY is not set",
            }
        _fernet()
        return {
            "configured": True,
            "valid": True,
            "encrypt_by_default": encryption_enabled_by_default(),
            "scheme": ENCRYPTION_SCHEME,
            "detail": "backup encryption key is valid",
        }
    except PermissionError as exc:
        return {
            "configured": True,
            "valid": False,
            "encrypt_by_default": encryption_enabled_by_default(),
            "scheme": ENCRYPTION_SCHEME,
            "detail": str(exc),
        }


def _fernet():
    key = os.getenv("AUTOOPS_BACKUP_ENCRYPTION_KEY", "").strip()
    if not key:
        raise PermissionError("AUTOOPS_BACKUP_ENCRYPTION_KEY is required for encrypted backups.")
    try:
        from cryptography.fernet import Fernet

        return Fernet(key.encode("utf-8"))
    except Exception as exc:
        raise PermissionError(f"Invalid AUTOOPS_BACKUP_ENCRYPTION_KEY: {exc}") from exc


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _job_to_dict(job: InvestigationJob) -> dict:
    return {
        "id": job.id,
        "goal": job.goal,
        "status": job.status,
        "current_step": job.current_step,
        "trace": job.trace or [],
        "result": job.result,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
    }


def _audit_to_dict(event: AuditEvent) -> dict:
    return {
        "id": event.id,
        "actor": event.actor,
        "action": event.action,
        "resource_type": event.resource_type,
        "resource_id": event.resource_id,
        "request_id": event.request_id,
        "metadata_json": event.metadata_json or {},
        "previous_hash": event.previous_hash,
        "event_hash": event.event_hash,
        "created_at": event.created_at.isoformat() if event.created_at else None,
    }


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def _sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_backup_path(backup_id: str) -> pathlib.Path:
    candidate = backup_root() / pathlib.Path(backup_id).name
    resolved = candidate.resolve()
    root = backup_root()
    if root not in resolved.parents or not (resolved.name.endswith(".zip") or resolved.name.endswith(".zip.enc")):
        raise ValueError("Invalid backup id.")
    return resolved


def _is_encrypted_backup(path: pathlib.Path) -> bool:
    return path.name.endswith(".zip.enc")


def _read_zip_bytes(path: pathlib.Path) -> bytes:
    data = path.read_bytes()
    if _is_encrypted_backup(path):
        return _fernet().decrypt(data)
    return data


def _configured_sources() -> list[tuple[str, pathlib.Path, bool]]:
    include_secrets = os.getenv("AUTOOPS_BACKUP_INCLUDE_SECRETS", "false").lower() == "true"
    sources = [
        ("inbox", pathlib.Path(os.getenv("AUTOOPS_INBOX", "inbox")), False),
        ("papers", pathlib.Path("papers"), False),
        ("notes", pathlib.Path("notes"), False),
        ("data", pathlib.Path("data"), False),
        ("chroma", pathlib.Path(os.getenv("CHROMA_PERSIST_DIR", "/root/.cache/chroma_data")), False),
    ]
    if include_secrets:
        token_path = pathlib.Path(os.getenv("GMAIL_TOKEN_PATH", ".gmail/token.json"))
        sources.append(("gmail_token", token_path, True))
    return sources


def _should_skip(path: pathlib.Path) -> bool:
    return path.name in EXCLUDED_NAMES or path.suffix == ".pyc"


def _add_path_to_zip(archive: zipfile.ZipFile, label: str, path: pathlib.Path) -> int:
    if not path.exists():
        return 0
    files_written = 0
    resolved = path.expanduser().resolve()
    if resolved.is_file():
        archive.write(resolved, f"files/{label}/{resolved.name}")
        return 1

    for file_path in sorted(resolved.rglob("*")):
        if not file_path.is_file() or any(_should_skip(parent) for parent in file_path.parents):
            continue
        if _should_skip(file_path):
            continue
        arcname = pathlib.PurePosixPath("files") / label / file_path.relative_to(resolved)
        archive.write(file_path, str(arcname))
        files_written += 1
    return files_written


def create_backup(db: Session, include_files: bool = True, encrypt: bool | None = None) -> dict:
    encrypt = encryption_enabled_by_default() if encrypt is None else encrypt
    root = ensure_backup_root()
    backup_id = backup_id_for_now()
    if encrypt:
        backup_id = f"{backup_id}.enc"
    destination = root / backup_id
    base_name = backup_id.removesuffix(".zip.enc").removesuffix(".zip")
    extension = ".zip.enc" if encrypt else ".zip"
    counter = 2
    while destination.exists():
        backup_id = f"{base_name}-{counter}{extension}"
        destination = root / backup_id
        counter += 1

    jobs = [_job_to_dict(job) for job in db.query(InvestigationJob).order_by(InvestigationJob.created_at).all()]
    audit_events = [_audit_to_dict(event) for event in db.query(AuditEvent).order_by(AuditEvent.created_at).all()]
    sources = _configured_sources() if include_files else []
    manifest = {
        "version": BACKUP_VERSION,
        "created_at": datetime.utcnow().isoformat(),
        "database": {
            "investigation_jobs": len(jobs),
            "audit_events": len(audit_events),
        },
        "include_files": include_files,
        "encrypted": encrypt,
        "encryption": ENCRYPTION_SCHEME if encrypt else None,
        "secrets_included": os.getenv("AUTOOPS_BACKUP_INCLUDE_SECRETS", "false").lower() == "true",
        "sources": [],
    }

    with tempfile.NamedTemporaryFile(prefix="autoops-backup-", suffix=".zip.tmp", dir=root, delete=False) as tmp:
        tmp_path = pathlib.Path(tmp.name)

    try:
        with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "database/investigation_jobs.json",
                json.dumps(jobs, indent=2, sort_keys=True, default=_json_default),
            )
            archive.writestr(
                "database/audit_events.json",
                json.dumps(audit_events, indent=2, sort_keys=True, default=_json_default),
            )

            for label, path, sensitive in sources:
                if sensitive and not manifest["secrets_included"]:
                    continue
                resolved = path.expanduser().resolve()
                file_count = _add_path_to_zip(archive, label, resolved)
                manifest["sources"].append({
                    "label": label,
                    "path": str(resolved),
                    "exists": resolved.exists(),
                    "files": file_count,
                    "sensitive": sensitive,
                })

            archive.writestr("manifest.json", json.dumps(manifest, indent=2, sort_keys=True))
        if encrypt:
            plaintext = tmp_path.read_bytes()
            encrypted = _fernet().encrypt(plaintext)
            destination.write_bytes(encrypted)
            tmp_path.unlink(missing_ok=True)
        else:
            tmp_path.replace(destination)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise

    return {
        "backup_id": destination.name,
        "path": str(destination),
        "size_bytes": destination.stat().st_size,
        "sha256": _sha256(destination),
        "encrypted": encrypt,
        "manifest": manifest,
    }


def list_backups() -> list[dict]:
    root = ensure_backup_root()
    backups = []
    paths = [*root.glob("*.zip"), *root.glob("*.zip.enc")]
    for path in sorted(paths, reverse=True):
        backups.append({
            "backup_id": path.name,
            "path": str(path),
            "size_bytes": path.stat().st_size,
            "sha256": _sha256(path),
            "encrypted": _is_encrypted_backup(path),
            "created_at": datetime.utcfromtimestamp(path.stat().st_mtime).isoformat(),
        })
    return backups


def inspect_backup(backup_id: str) -> dict:
    path = _safe_backup_path(backup_id)
    if not path.exists():
        raise FileNotFoundError(backup_id)
    zip_bytes = _read_zip_bytes(path)
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        names = archive.namelist()
        manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
    return {
        "backup_id": path.name,
        "path": str(path),
        "size_bytes": path.stat().st_size,
        "sha256": _sha256(path),
        "plaintext_sha256": _sha256_bytes(zip_bytes) if _is_encrypted_backup(path) else None,
        "encrypted": _is_encrypted_backup(path),
        "manifest": manifest,
        "entries": len(names),
    }


def restore_backup(db: Session, backup_id: str, dry_run: bool = True) -> dict:
    if not dry_run and os.getenv("AUTOOPS_ENABLE_RESTORE", "false").lower() != "true":
        raise PermissionError("Set AUTOOPS_ENABLE_RESTORE=true to run a non-dry-run restore.")

    path = _safe_backup_path(backup_id)
    if not path.exists():
        raise FileNotFoundError(backup_id)

    with zipfile.ZipFile(io.BytesIO(_read_zip_bytes(path))) as archive:
        jobs = json.loads(archive.read("database/investigation_jobs.json").decode("utf-8"))

    job_ids = [job["id"] for job in jobs]
    existing_ids = set()
    if job_ids:
        existing_ids = {
            row[0]
            for row in db.query(InvestigationJob.id).filter(InvestigationJob.id.in_(job_ids)).all()
        }
    to_insert = [job for job in jobs if job["id"] not in existing_ids]
    to_update = [job for job in jobs if job["id"] in existing_ids]

    if not dry_run:
        for data in jobs:
            job = db.query(InvestigationJob).filter(InvestigationJob.id == data["id"]).first()
            if job is None:
                job = InvestigationJob(id=data["id"], goal=data["goal"])
                db.add(job)
            job.goal = data["goal"]
            job.status = data["status"]
            job.current_step = data.get("current_step")
            job.trace = data.get("trace") or []
            job.result = data.get("result")
            job.created_at = _parse_datetime(data.get("created_at"))
            job.updated_at = _parse_datetime(data.get("updated_at"))
        db.commit()

    return {
        "backup_id": path.name,
        "dry_run": dry_run,
        "jobs_seen": len(jobs),
        "jobs_to_insert": len(to_insert),
        "jobs_to_update": len(to_update),
        "restored": not dry_run,
    }
