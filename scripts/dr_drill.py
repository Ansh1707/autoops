"""Run an isolated AutoOps disaster-recovery backup/restore drill."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import tempfile
import time
import sys
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Iterator

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.backups import create_backup, inspect_backup, restore_backup
from api.migrations import ensure_schema
from api.models import AuditEvent, InvestigationJob


@dataclass(frozen=True)
class DrillCheck:
    name: str
    ok: bool
    detail: str


@contextmanager
def _temporary_env(values: dict[str, str]) -> Iterator[None]:
    previous = {key: os.getenv(key) for key in values}
    try:
        for key, value in values.items():
            os.environ[key] = value
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ensure_schema(engine)
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)


def _seed_source_db(Session) -> None:
    with Session() as db:
        db.add_all([
            InvestigationJob(
                id="dr-job-success",
                goal="DR drill successful job",
                status="SUCCESS",
                trace=[{"type": "ai", "content": "drill ok"}],
                result="Recovered successfully",
                created_at=datetime(2026, 7, 5, 1, 0, 0),
                updated_at=datetime(2026, 7, 5, 1, 1, 0),
            ),
            InvestigationJob(
                id="dr-job-running",
                goal="DR drill active job",
                status="RUNNING",
                current_step="Step 1 - drill",
            ),
            AuditEvent(
                actor="drill",
                action="drill.seed",
                resource_type="backup",
                resource_id="drill",
                event_hash="d" * 64,
            ),
        ])
        db.commit()


def run_dr_drill(workdir: pathlib.Path | None = None, encrypt: bool = False) -> dict:
    started = time.monotonic()
    checks: list[DrillCheck] = []
    with tempfile.TemporaryDirectory(prefix="autoops-drill-", dir=workdir) as tmpdir:
        root = pathlib.Path(tmpdir)
        backup_dir = root / "backups"
        inbox = root / "inbox"
        chroma = root / "chroma"
        papers = root / "papers"
        notes = root / "notes"
        data = root / "data"
        inbox.mkdir()
        chroma.mkdir()
        papers.mkdir()
        notes.mkdir()
        data.mkdir()
        (inbox / "drill-note.txt").write_text("restore drill input", encoding="utf-8")
        (chroma / "drill-index.txt").write_text("vector data", encoding="utf-8")

        env = {
            "AUTOOPS_BACKUP_DIR": str(backup_dir),
            "AUTOOPS_INBOX": str(inbox),
            "CHROMA_PERSIST_DIR": str(chroma),
            "AUTOOPS_ENABLE_RESTORE": "true",
        }
        if encrypt:
            from cryptography.fernet import Fernet

            env["AUTOOPS_BACKUP_ENCRYPTION_KEY"] = Fernet.generate_key().decode("utf-8")

        with _temporary_env(env):
            SourceSession = _session_factory()
            TargetSession = _session_factory()
            _seed_source_db(SourceSession)

            old_cwd = pathlib.Path.cwd()
            try:
                os.chdir(root)
                with SourceSession() as source_db:
                    backup = create_backup(source_db, include_files=True, encrypt=encrypt)
                checks.append(DrillCheck("backup_created", backup["size_bytes"] > 0, backup["backup_id"]))
                checks.append(DrillCheck("backup_checksum", len(backup["sha256"]) == 64, backup["sha256"]))

                inspected = inspect_backup(backup["backup_id"])
                manifest = inspected["manifest"]
                checks.append(
                    DrillCheck(
                        "manifest_counts",
                        manifest["database"]["investigation_jobs"] == 2 and manifest["database"]["audit_events"] == 1,
                        json.dumps(manifest["database"], sort_keys=True),
                    )
                )
                checks.append(
                    DrillCheck(
                        "file_payload_present",
                        any(source["label"] == "inbox" and source["files"] >= 1 for source in manifest["sources"]),
                        json.dumps(manifest["sources"], sort_keys=True),
                    )
                )

                with TargetSession() as target_db:
                    dry_run = restore_backup(target_db, backup["backup_id"], dry_run=True)
                    checks.append(
                        DrillCheck(
                            "dry_run_restore_counts",
                            dry_run["jobs_seen"] == 2 and dry_run["jobs_to_insert"] == 2 and dry_run["restored"] is False,
                            json.dumps(dry_run, sort_keys=True),
                        )
                    )
                    real_restore = restore_backup(target_db, backup["backup_id"], dry_run=False)
                    restored_jobs = target_db.query(InvestigationJob).count()
                    checks.append(
                        DrillCheck(
                            "real_restore_isolated_target",
                            real_restore["restored"] is True and restored_jobs == 2,
                            json.dumps({"restore": real_restore, "restored_jobs": restored_jobs}, sort_keys=True),
                        )
                    )
            finally:
                os.chdir(old_cwd)

    failed = [check for check in checks if not check.ok]
    return {
        "ok": not failed,
        "duration_ms": int((time.monotonic() - started) * 1000),
        "encrypted": encrypt,
        "checks": [asdict(check) for check in checks],
        "passed": len(checks) - len(failed),
        "failed": len(failed),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run an isolated AutoOps disaster-recovery drill.")
    parser.add_argument("--workdir", type=pathlib.Path, help="Optional parent directory for temporary drill state.")
    parser.add_argument("--encrypt", action="store_true", help="Exercise encrypted backup creation and restore.")
    args = parser.parse_args()

    summary = run_dr_drill(workdir=args.workdir, encrypt=args.encrypt)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
