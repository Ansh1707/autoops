from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.metrics import MetricsRegistry, job_metrics
from api.migrations import ensure_schema
from api.models import AuditEvent, InvestigationJob
from api.status import JobStatus


def _db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ensure_schema(engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return Session()


def test_job_metrics_counts_jobs_and_audit_events():
    with _db_session() as db:
        db.add_all([
            InvestigationJob(goal="queued", status=JobStatus.QUEUED.value),
            InvestigationJob(goal="success", status=JobStatus.SUCCESS.value),
            AuditEvent(
                actor="tester",
                action="unit.test",
                resource_type="job",
                event_hash="hash",
            ),
        ])
        db.commit()

        metrics = job_metrics(db)

    assert metrics.jobs_total == 2
    assert metrics.jobs_active == 1
    assert metrics.jobs_terminal == 1
    assert metrics.audit_events_total == 1


def test_prometheus_text_escapes_labels_and_emits_summary():
    registry = MetricsRegistry()
    registry.increment("autoops_api_requests_total", method="GET", path='/quote"path', status_code="200")
    registry.observe("autoops_api_request_duration", 0.25, method="GET", path="/health")

    with _db_session() as db:
        db.add(InvestigationJob(goal="quoted", status='BAD"STATUS'))
        db.commit()
        text = registry.prometheus_text(job_metrics(db))

    assert 'autoops_jobs_total{status="BAD\\"STATUS"} 1' in text
    assert 'path="/quote\\"path"' in text
    assert "autoops_api_request_duration_seconds_count" in text
    assert "autoops_api_request_duration_seconds_sum" in text
