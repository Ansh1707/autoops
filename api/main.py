from contextlib import asynccontextmanager
import os
import time
import uuid

from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy.exc import OperationalError
from sqlalchemy import text
from sqlalchemy.orm import Session
from pydantic import BaseModel

from agent.observability import log_event
from api.audit import actor_from_claims, list_audit_events, record_audit_event, verify_audit_chain
from api.auth import ROLE_ORDER, authenticate_user, bootstrap_default_user, create_access_token, hash_password, require_roles, verify_token
from api.backups import create_backup, inspect_backup, list_backups, restore_backup
from api.migrations import ensure_schema
from api.metrics import job_metrics, metrics_registry
from api.models import AuditEventResponse, SessionLocal, engine, InvestigationJob, GoalRequest, JobResponse, UserResponse
from api.preflight import run_preflight
from api.rate_limit import check_global_rate_limit, enforce_job_submit_rate_limit, headers_for
from api.slo import evaluate_slos
from api.status import JobStatus
from api.version import build_metadata


def cors_origins() -> list[str]:
    raw = os.getenv("AUTOOPS_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
    origins = [origin.strip() for origin in raw.split(",") if origin.strip()]
    if "*" in origins and os.getenv("AUTOOPS_ENV", "development").lower() in {"prod", "production"}:
        raise RuntimeError("Wildcard CORS is not allowed in production.")
    return origins or ["http://localhost:5173"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db_with_retry()
    yield


app = FastAPI(
    title="AutoOps API",
    version=build_metadata()["version"],
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_observability(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    started_at = time.monotonic()
    client_host = request.client.host if request.client else "unknown"
    rate_limit_result = check_global_rate_limit(client_host)
    if not rate_limit_result.allowed:
        metrics_registry.increment(
            "autoops_api_rate_limit_blocks_total",
            scope="global",
            path=request.url.path,
        )
        response = JSONResponse(
            status_code=429,
            content={"detail": "Rate limit exceeded."},
            headers=headers_for(rate_limit_result),
        )
    else:
        response = await call_next(request)
    duration_ms = int((time.monotonic() - started_at) * 1000)
    response.headers["X-Request-ID"] = request_id
    metrics_registry.increment(
        "autoops_api_requests_total",
        method=request.method,
        path=request.url.path,
        status_code=str(response.status_code),
    )
    metrics_registry.observe(
        "autoops_api_request_duration",
        duration_ms / 1000,
        method=request.method,
        path=request.url.path,
    )
    log_event(
        "api_request",
        request_id=request_id,
        method=request.method,
        path=request.url.path,
        status_code=response.status_code,
        duration_ms=duration_ms,
    )
    return response


def request_id_from(request: Request) -> str | None:
    return request.headers.get("X-Request-ID")


def init_db_with_retry(max_attempts: int = 30, delay_seconds: int = 2):
    """Wait for Postgres/Docker DNS, then create tables."""
    for attempt in range(1, max_attempts + 1):
        try:
            ensure_schema(engine)
            with SessionLocal() as db:
                bootstrap_default_user(db)
            return
        except OperationalError as exc:
            if attempt == max_attempts:
                raise
            print(
                f"Database not ready yet "
                f"({attempt}/{max_attempts}): {exc}. Retrying in {delay_seconds}s..."
            )
            time.sleep(delay_seconds)

# Dependency to get a database session for each request
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class LoginRequest(BaseModel):
    username: str
    password: str


class IngestRequest(BaseModel):
    file_path: str
    doc_type: str = "note"


class BackupCreateRequest(BaseModel):
    include_files: bool = True
    encrypt: bool | None = None


class RestoreRequest(BaseModel):
    dry_run: bool = True


class UserCreateRequest(BaseModel):
    username: str
    password: str
    role: str = "viewer"


@app.get("/health")
async def health_check():
    return {"status": "ok"}


@app.get("/version")
async def version():
    return build_metadata()


@app.get("/ready")
async def readiness_check(db: Session = Depends(get_db)):
    """Return ready only when the API can execute a lightweight DB query."""
    try:
        db.execute(text("SELECT 1"))
        return {"status": "ready"}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Database not ready: {exc}")


@app.get("/metrics")
async def metrics(db: Session = Depends(get_db)):
    """Return lightweight operational metrics for local dashboards and debugging."""
    jobs = job_metrics(db)
    return {
        "jobs_total": jobs.jobs_total,
        "jobs_active": jobs.jobs_active,
        "jobs_terminal": jobs.jobs_terminal,
        "jobs_by_status": jobs.jobs_by_status,
        "audit_events_total": jobs.audit_events_total,
        "runtime": metrics_registry.snapshot(),
    }


@app.get("/metrics/prometheus", response_class=PlainTextResponse)
async def prometheus_metrics(db: Session = Depends(get_db)):
    """Return Prometheus-compatible metrics text for local or cluster scraping."""
    return PlainTextResponse(
        metrics_registry.prometheus_text(job_metrics(db)),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


@app.get("/slo")
async def slo_status(db: Session = Depends(get_db)):
    """Return machine-readable SLO status for dashboards and alert triage."""
    return evaluate_slos(job_metrics(db), metrics_registry)


@app.get("/audit", response_model=list[AuditEventResponse])
async def audit_events(
    limit: int = 50,
    claims: dict = Depends(require_roles("viewer")),
    db: Session = Depends(get_db),
):
    """Return recent audit events for user-visible accountability."""
    return list_audit_events(db, limit=limit)


@app.get("/audit/verify")
async def audit_verify(
    claims: dict = Depends(require_roles("viewer")),
    db: Session = Depends(get_db),
):
    """Verify the append-only audit hash chain."""
    return verify_audit_chain(db)


@app.get("/preflight")
async def preflight(db: Session = Depends(get_db)):
    """Return startup diagnostics for required services and optional capabilities."""
    return run_preflight(db)


@app.get("/backups")
async def backups(
    request: Request,
    claims: dict = Depends(require_roles("viewer")),
    db: Session = Depends(get_db),
):
    """List local AutoOps backup artifacts."""
    result = {"backups": list_backups()}
    record_audit_event(
        db,
        actor=actor_from_claims(claims),
        action="backup.list",
        resource_type="backup",
        request_id=request_id_from(request),
        metadata={"count": len(result["backups"])},
    )
    return result


@app.post("/backups")
async def create_backup_endpoint(
    payload: BackupCreateRequest,
    request: Request,
    claims: dict = Depends(require_roles("operator")),
    db: Session = Depends(get_db),
):
    """Create a checksummed local backup of AutoOps-owned state."""
    try:
        result = create_backup(db, include_files=payload.include_files, encrypt=payload.encrypt)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    record_audit_event(
        db,
        actor=actor_from_claims(claims),
        action="backup.create",
        resource_type="backup",
        resource_id=result["backup_id"],
        request_id=request_id_from(request),
        metadata={
            "include_files": payload.include_files,
            "encrypted": result["encrypted"],
            "size_bytes": result["size_bytes"],
            "sha256": result["sha256"],
            "jobs": result["manifest"]["database"]["investigation_jobs"],
        },
    )
    return result


@app.get("/backups/{backup_id}")
async def inspect_backup_endpoint(
    backup_id: str,
    request: Request,
    claims: dict = Depends(require_roles("viewer")),
    db: Session = Depends(get_db),
):
    """Inspect backup metadata without restoring it."""
    try:
        result = inspect_backup(backup_id)
        record_audit_event(
            db,
            actor=actor_from_claims(claims),
            action="backup.inspect",
            resource_type="backup",
            resource_id=backup_id,
            request_id=request_id_from(request),
            metadata={"entries": result["entries"], "sha256": result["sha256"]},
        )
        return result
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Backup not found")
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/backups/{backup_id}/restore")
async def restore_backup_endpoint(
    backup_id: str,
    payload: RestoreRequest,
    request: Request,
    claims: dict = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
):
    """Dry-run restore by default; real restore requires AUTOOPS_ENABLE_RESTORE=true."""
    try:
        result = restore_backup(db, backup_id, dry_run=payload.dry_run)
        record_audit_event(
            db,
            actor=actor_from_claims(claims),
            action="backup.restore",
            resource_type="backup",
            resource_id=backup_id,
            request_id=request_id_from(request),
            metadata=result,
        )
        return result
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Backup not found")
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def enqueue_investigation(goal: str, job_id: str) -> None:
    """Submit work to Celery without importing the worker/agent at API import time."""
    from worker.tasks import investigate_task

    investigate_task.apply_async(args=[goal], task_id=job_id)


@app.post("/token")
async def login(request: LoginRequest, db: Session = Depends(get_db)):
    """Issue a signed JWT for local/demo use."""
    user = authenticate_user(db, request.username, request.password)
    if user:
        return {
            "access_token": create_access_token(data={
                "sub": user.username,
                "username": user.username,
                "user_id": user.id,
                "role": user.role,
            }),
            "token_type": "bearer",
            "role": user.role,
        }
    raise HTTPException(status_code=401, detail="Invalid credentials")


@app.get("/users/me", response_model=UserResponse)
async def current_user_profile(claims: dict = Depends(require_roles("viewer")), db: Session = Depends(get_db)):
    from api.models import User

    username = claims.get("sub") or claims.get("username")
    user = db.query(User).filter(User.username == username).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@app.get("/users", response_model=list[UserResponse])
async def list_users(claims: dict = Depends(require_roles("admin")), db: Session = Depends(get_db)):
    from api.models import User

    return db.query(User).order_by(User.username).all()


@app.post("/users", response_model=UserResponse)
async def create_user(
    payload: UserCreateRequest,
    request: Request,
    claims: dict = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
):
    from api.models import User

    username = payload.username.strip()
    if not username:
        raise HTTPException(status_code=400, detail="Username is required")
    if payload.role not in ROLE_ORDER:
        raise HTTPException(status_code=400, detail=f"Invalid role: {payload.role}")
    if db.query(User).filter(User.username == username).first():
        raise HTTPException(status_code=409, detail="User already exists")
    user = User(
        username=username,
        password_hash=hash_password(payload.password),
        role=payload.role,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    record_audit_event(
        db,
        actor=actor_from_claims(claims),
        action="user.create",
        resource_type="user",
        resource_id=user.id,
        request_id=request_id_from(request),
        metadata={"username": user.username, "role": user.role},
    )
    return user

@app.post("/investigate")
async def submit_investigation(
    payload: GoalRequest,
    request: Request,
    claims: dict = Depends(require_roles("operator")),
    db: Session = Depends(get_db),
):
    """Submit a goal, save to DB, and hand off to Celery."""
    actor = actor_from_claims(claims)
    enforce_job_submit_rate_limit(actor)

    # 1. Create the database record
    new_job = InvestigationJob(
        goal=payload.goal,
        status=JobStatus.QUEUED.value,
    )
    db.add(new_job)
    db.commit()
    db.refresh(new_job)
    record_audit_event(
        db,
        actor=actor,
        action="investigation.submit",
        resource_type="job",
        resource_id=new_job.id,
        request_id=request_id_from(request),
        metadata={"goal_preview": payload.goal[:200]},
    )

    # 2. Trigger Celery (passing the DB ID so the worker can update it later)
    enqueue_investigation(payload.goal, new_job.id)

    return {"job_id": new_job.id, "status": JobStatus.QUEUED.value}


@app.get("/jobs", response_model=list[JobResponse])
async def list_jobs(
    limit: int = 25,
    claims: dict = Depends(require_roles("viewer")),
    db: Session = Depends(get_db),
):
    """Return recent investigation jobs for operator dashboards."""
    bounded_limit = min(max(limit, 1), 100)
    return (
        db.query(InvestigationJob)
        .order_by(InvestigationJob.updated_at.desc(), InvestigationJob.created_at.desc())
        .limit(bounded_limit)
        .all()
    )


@app.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job_status(
    job_id: str,
    claims: dict = Depends(require_roles("viewer")),
    db: Session = Depends(get_db),
):
    """Fetch the investigation status and trace from the database."""
    job = db.query(InvestigationJob).filter(InvestigationJob.id == job_id).first()

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    return job


@app.post("/ingest")
async def ingest_local_document(
    payload: IngestRequest,
    request: Request,
    claims: dict = Depends(require_roles("operator")),
    db: Session = Depends(get_db),
):
    """Index a local text/markdown/PDF document into ChromaDB."""
    from agent.tool_domains.documents import ingest_document

    result = ingest_document.invoke({
        "file_path": payload.file_path,
        "doc_type": payload.doc_type,
    })
    record_audit_event(
        db,
        actor=actor_from_claims(claims),
        action="document.ingest",
        resource_type="document",
        resource_id=payload.file_path,
        request_id=request_id_from(request),
        metadata={"doc_type": payload.doc_type, "result_preview": str(result)[:500]},
    )
    return {"status": "ok", "result": result}
