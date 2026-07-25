from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.audit import record_audit_event, sanitize_metadata, verify_audit_chain
from api.migrations import ensure_schema
from api.models import AuditEvent


def _session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ensure_schema(engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return Session()


def test_sanitize_metadata_redacts_sensitive_values():
    clean = sanitize_metadata({
        "file_path": "/app/inbox/doc.pdf",
        "password": "secret",
        "nested": {"access_token": "abc", "safe": True},
    })

    assert clean == {
        "file_path": "/app/inbox/doc.pdf",
        "password": "[REDACTED]",
        "nested": {"access_token": "[REDACTED]", "safe": True},
    }


def test_audit_chain_verifies_and_detects_tampering():
    with _session() as db:
        first = record_audit_event(
            db,
            actor="admin",
            action="backup.create",
            resource_type="backup",
            resource_id="one.zip",
            metadata={"password": "should-not-leak"},
        )
        second = record_audit_event(
            db,
            actor="admin",
            action="backup.restore",
            resource_type="backup",
            resource_id="one.zip",
            metadata={"dry_run": True},
        )

        assert len(first.event_hash) == 64
        assert second.previous_hash == first.event_hash
        assert first.metadata_json["password"] == "[REDACTED]"
        assert verify_audit_chain(db)["ok"] is True

        second.action = "backup.restore.tampered"
        db.commit()

        verification = verify_audit_chain(db)
        assert verification["ok"] is False
        assert verification["failure"] == "event_hash_mismatch"
        assert verification["event_id"] == second.id


def test_audit_table_is_created_by_schema():
    with _session() as db:
        db.add(
            AuditEvent(
                actor="system",
                action="test",
                resource_type="unit",
                event_hash="0" * 64,
            )
        )
        db.commit()

        assert db.query(AuditEvent).count() == 1
