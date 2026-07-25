"""
worker/tasks.py

Celery task definitions.

Key change from v3:
  investigate_task now defines a step_callback closure and passes it into
  run_investigation. After each tool fires, the callback writes a granular
  status string (e.g. "STEP_3:get_metrics") to Postgres via the isolated
  NullPool engine — the same engine already used for all worker DB writes.

  Status lifecycle for a single job:
    QUEUED  → set by the API when the Celery task is enqueued
    PLANNING → set at the start of the task, before the agent loop begins
    STEP_N:tool_name → set after each tool execution (N = 1, 2, 3 …)
    REFLECTING → set when the agent enters the reflection node
    SUCCESS / FAILED → set when the loop finishes

  The frontend polls /jobs/{job_id} every 2 seconds and reads the status
  field, showing the user exactly which tool the agent is running right now.
"""

import os

from celery import Celery
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from api.models import InvestigationJob
from api.status import JobStatus

redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
db_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@postgres:5432/autoops")

celery_app = Celery(
    "autoops_worker",
    broker=redis_url,
    backend=redis_url,
)
celery_app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_track_started=True,
    worker_prefetch_multiplier=1,
    task_soft_time_limit=int(os.getenv("AUTOOPS_TASK_SOFT_TIME_LIMIT", "900")),
    task_time_limit=int(os.getenv("AUTOOPS_TASK_TIME_LIMIT", "960")),
)

# ── Celery Beat schedule ───────────────────────────────────────────────────────
# watchdog_task runs every 5 minutes to check local system health.
celery_app.conf.beat_schedule = {
    "local-watchdog-every-5-minutes": {
        "task": "worker.tasks.watchdog_task",
        "schedule": 300.0,
    }
}

# ── Isolated DB engine ─────────────────────────────────────────────────────────
# NullPool ensures every write gets a completely fresh connection.
# This is required in Celery workers because forked processes must never
# inherit connection pool state from the parent process.
isolated_engine = create_engine(db_url, poolclass=NullPool)
IsolatedSession = sessionmaker(bind=isolated_engine)
UNCHANGED = object()


def update_job_status(
    job_id: str,
    status: str,
    result: str | None = None,
    trace: list | None = None,
    current_step: str | None | object = UNCHANGED,
) -> None:
    """
    Write job state to Postgres using a fresh, un-pooled connection.

    current_step is the human-readable label shown live in the frontend
    while the agent is executing (e.g. "Step 3 — get_metrics").
    It is stored in the current_step column added to InvestigationJob.
    """
    with IsolatedSession() as db:
        job = db.query(InvestigationJob).filter(InvestigationJob.id == job_id).first()
        if job:
            job.status = status
            if result is not None:
                job.result = result
            if trace is not None:
                job.trace = trace
            if current_step is not UNCHANGED:
                job.current_step = current_step
            db.commit()


@celery_app.task(bind=True)
def investigate_task(self, goal: str) -> dict:
    """
    Main AI investigation task.

    Lifecycle:
    1. Write PLANNING status so the frontend immediately shows activity.
    2. Define step_callback — called by tool_node after every tool execution.
       Each call writes STEP_N:tool_name to the DB, updating the live status.
    3. Run the agent loop via run_investigation().
    4. On success, write SUCCESS + final result + full trace.
    5. On failure, write FAILED + error message.
    """
    job_id: str = self.request.id

    # ── Phase 1: PLANNING ─────────────────────────────────────────────────────
    update_job_status(
        job_id,
        status=JobStatus.PLANNING.value,
        current_step="Agent is building an investigation plan…",
    )

    # ── Phase 2: Define the live-status callback ───────────────────────────────
    def step_callback(step_number: int, tool_name: str) -> None:
        """
        Called by tool_node in planner.py after each tool execution.
        Writes granular progress to the DB so the frontend can display it.

        Example values written to status + current_step:
          status       = "RUNNING"
          current_step = "Step 2 — search_past_incidents"
        """
        update_job_status(
            job_id,
            status=JobStatus.RUNNING.value,
            current_step=f"Step {step_number} — {tool_name}",
        )

    try:
        from agent.planner import run_investigation

        # ── Phase 3: Agent loop ────────────────────────────────────────────────
        # Pass the callback so every tool execution triggers a live DB write.
        result, trace_log = run_investigation(
            goal,
            job_id=job_id,
            step_callback=step_callback,
        )

        # ── Phase 4: Write reflection status briefly before SUCCESS ────────────
        # This lets the frontend show "Reflecting…" during the reflection node.
        # We can't easily hook into reflection_node without threading the callback
        # deeper, so we approximate it by writing REFLECTING just before SUCCESS.
        update_job_status(
            job_id,
            status=JobStatus.REFLECTING.value,
            current_step="Agent is reviewing its own findings…",
        )

        # ── Phase 5: SUCCESS ───────────────────────────────────────────────────
        update_job_status(
            job_id,
            status=JobStatus.SUCCESS.value,
            result=result,
            trace=trace_log,
            current_step=None,
        )
        return {"result": "success"}

    except Exception as exc:
        update_job_status(
            job_id,
            status=JobStatus.FAILED.value,
            result=f"Worker error: {str(exc)}",
            current_step=None,
        )
        return {"error": str(exc)}


@celery_app.task(name="worker.tasks.watchdog_task")
def watchdog_task() -> dict:
    """
    Scheduled local health check — runs every 5 minutes via Celery Beat.

    Calls get_system_stats and passes the output to run_investigation.
    If the agent finds anything critical (CPU/memory/disk spike, runaway
    process), it writes a warning to the watchdog job record.
    The frontend can surface recent watchdog results in a dedicated panel.
    """
    from agent.tool_domains.system import get_system_stats

    stats = get_system_stats.invoke({})
    goal = (
        "Review these local system stats. "
        "If CPU, memory, disk, or any process looks critical, "
        "write a concise warning with the likely cause and recommended next action. "
        "If everything looks healthy, reply with: OK — all systems normal.\n\n"
        f"Stats:\n{stats}"
    )

    from agent.planner import run_investigation

    result, trace_log = run_investigation(goal, job_id="watchdog")
    return {"result": result, "trace_length": len(trace_log)}
