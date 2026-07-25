from datetime import datetime
import json
import zipfile

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.backups import create_backup, inspect_backup, list_backups, restore_backup
from api.migrations import ensure_schema
from api.models import AuditEvent, InvestigationJob


def _session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ensure_schema(engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return Session()


def test_create_backup_writes_manifest_database_and_files(monkeypatch, tmp_path):
    backup_dir = tmp_path / "backups"
    inbox = tmp_path / "inbox"
    chroma = tmp_path / "chroma"
    inbox.mkdir()
    chroma.mkdir()
    (inbox / "note.txt").write_text("remember this", encoding="utf-8")
    (chroma / "index.txt").write_text("vector data", encoding="utf-8")

    monkeypatch.setenv("AUTOOPS_BACKUP_DIR", str(backup_dir))
    monkeypatch.setenv("AUTOOPS_INBOX", str(inbox))
    monkeypatch.setenv("CHROMA_PERSIST_DIR", str(chroma))

    with _session() as db:
        db.add(
            InvestigationJob(
                id="job-1",
                goal="backup me",
                status="SUCCESS",
                trace=[{"type": "ai", "content": "ok"}],
                result="done",
                created_at=datetime(2026, 7, 5, 1, 2, 3),
                updated_at=datetime(2026, 7, 5, 1, 2, 4),
            )
        )
        db.add(
            AuditEvent(
                actor="admin",
                action="test.audit",
                resource_type="unit",
                event_hash="1" * 64,
            )
        )
        db.commit()
        result = create_backup(db)

    assert result["backup_id"].endswith(".zip")
    assert len(result["sha256"]) == 64
    with zipfile.ZipFile(result["path"]) as archive:
        names = set(archive.namelist())
        manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        jobs = json.loads(archive.read("database/investigation_jobs.json").decode("utf-8"))
        audit_events = json.loads(archive.read("database/audit_events.json").decode("utf-8"))

    assert "files/inbox/note.txt" in names
    assert "files/chroma/index.txt" in names
    assert manifest["database"]["investigation_jobs"] == 1
    assert manifest["database"]["audit_events"] == 1
    assert manifest["secrets_included"] is False
    assert jobs[0]["id"] == "job-1"
    assert audit_events[0]["action"] == "test.audit"


def test_list_and_inspect_backups(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOOPS_BACKUP_DIR", str(tmp_path / "backups"))

    with _session() as db:
        result = create_backup(db, include_files=False)

    listed = list_backups()
    inspected = inspect_backup(result["backup_id"])

    assert [backup["backup_id"] for backup in listed] == [result["backup_id"]]
    assert inspected["backup_id"] == result["backup_id"]
    assert inspected["manifest"]["include_files"] is False


def test_create_encrypted_backup_requires_key(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOOPS_BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.delenv("AUTOOPS_BACKUP_ENCRYPTION_KEY", raising=False)

    with _session() as db:
        with pytest.raises(PermissionError, match="AUTOOPS_BACKUP_ENCRYPTION_KEY"):
            create_backup(db, include_files=False, encrypt=True)


def test_encrypted_backup_can_be_listed_inspected_and_restored(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOOPS_BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setenv("AUTOOPS_BACKUP_ENCRYPTION_KEY", Fernet.generate_key().decode("utf-8"))

    with _session() as source_db:
        source_db.add(InvestigationJob(id="job-enc", goal="encrypted restore", status="SUCCESS"))
        source_db.commit()
        result = create_backup(source_db, include_files=False, encrypt=True)

    assert result["backup_id"].endswith(".zip.enc")
    assert result["encrypted"] is True
    assert result["manifest"]["encrypted"] is True
    with pytest.raises(zipfile.BadZipFile):
        with zipfile.ZipFile(result["path"]):
            pass

    listed = list_backups()
    inspected = inspect_backup(result["backup_id"])
    assert listed[0]["encrypted"] is True
    assert inspected["encrypted"] is True
    assert inspected["plaintext_sha256"]
    assert inspected["manifest"]["encryption"] == "fernet-v1"

    with _session() as target_db:
        dry_run = restore_backup(target_db, result["backup_id"])
        assert dry_run["jobs_seen"] == 1
        assert dry_run["jobs_to_insert"] == 1


def test_restore_backup_is_dry_run_by_default(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOOPS_BACKUP_DIR", str(tmp_path / "backups"))

    with _session() as source_db:
        source_db.add(InvestigationJob(id="job-1", goal="restore me", status="SUCCESS"))
        source_db.commit()
        result = create_backup(source_db, include_files=False)

    with _session() as target_db:
        dry_run = restore_backup(target_db, result["backup_id"])
        assert dry_run == {
            "backup_id": result["backup_id"],
            "dry_run": True,
            "jobs_seen": 1,
            "jobs_to_insert": 1,
            "jobs_to_update": 0,
            "restored": False,
        }
        assert target_db.query(InvestigationJob).count() == 0


def test_real_restore_requires_explicit_enable(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOOPS_BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.delenv("AUTOOPS_ENABLE_RESTORE", raising=False)

    with _session() as source_db:
        result = create_backup(source_db, include_files=False)

    with _session() as target_db:
        with pytest.raises(PermissionError, match="AUTOOPS_ENABLE_RESTORE"):
            restore_backup(target_db, result["backup_id"], dry_run=False)


def test_real_restore_upserts_jobs_when_enabled(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOOPS_BACKUP_DIR", str(tmp_path / "backups"))

    with _session() as source_db:
        source_db.add(InvestigationJob(id="job-1", goal="restored", status="SUCCESS", result="ok"))
        source_db.commit()
        result = create_backup(source_db, include_files=False)

    monkeypatch.setenv("AUTOOPS_ENABLE_RESTORE", "true")
    with _session() as target_db:
        restore_result = restore_backup(target_db, result["backup_id"], dry_run=False)
        job = target_db.query(InvestigationJob).filter(InvestigationJob.id == "job-1").first()

    assert restore_result["restored"] is True
    assert job.goal == "restored"
    assert job.result == "ok"
