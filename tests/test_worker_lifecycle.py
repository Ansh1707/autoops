import os
import tempfile

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("CHROMA_PERSIST_DIR", tempfile.mkdtemp(prefix="autoops-test-chroma-"))

from api.models import Base, InvestigationJob
from api.status import JobStatus
from worker import tasks as worker_tasks


def _session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)


def test_update_job_status_preserves_current_step_when_omitted(monkeypatch):
    session_factory = _session_factory()
    monkeypatch.setattr(worker_tasks, "IsolatedSession", session_factory)

    with session_factory() as db:
        db.add(InvestigationJob(id="job-1", goal="test", status=JobStatus.RUNNING.value, current_step="Step 1"))
        db.commit()

    worker_tasks.update_job_status("job-1", JobStatus.RUNNING.value, result="partial")

    with session_factory() as db:
        job = db.query(InvestigationJob).filter(InvestigationJob.id == "job-1").first()
        assert job.result == "partial"
        assert job.current_step == "Step 1"


def test_update_job_status_can_clear_current_step_on_terminal_state(monkeypatch):
    session_factory = _session_factory()
    monkeypatch.setattr(worker_tasks, "IsolatedSession", session_factory)

    with session_factory() as db:
        db.add(InvestigationJob(id="job-2", goal="test", status=JobStatus.RUNNING.value, current_step="Step 2"))
        db.commit()

    worker_tasks.update_job_status(
        "job-2",
        JobStatus.SUCCESS.value,
        result="done",
        trace=[{"type": "ai", "content": "done"}],
        current_step=None,
    )

    with session_factory() as db:
        job = db.query(InvestigationJob).filter(InvestigationJob.id == "job-2").first()
        assert job.status == JobStatus.SUCCESS.value
        assert job.result == "done"
        assert job.trace == [{"type": "ai", "content": "done"}]
        assert job.current_step is None
