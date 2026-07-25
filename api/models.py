"""SQLAlchemy database model and Pydantic API schemas."""

import os
import uuid
from datetime import datetime
from typing import Any, List, Optional

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Boolean, Column, DateTime, JSON, String, Text, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from api.secrets import apply_encrypted_secrets

if os.getenv("AUTOOPS_LOAD_DOTENV", "true").lower() == "true":
    load_dotenv()
apply_encrypted_secrets()

# ── Database setup ─────────────────────────────────────────────────────────────

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/autoops",
)

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,   # recycle stale connections silently
    pool_recycle=300,     # force-recycle after 5 minutes
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# ── SQLAlchemy model ───────────────────────────────────────────────────────────

class InvestigationJob(Base):
    """Represents one investigation job row in the investigation_jobs table."""

    __tablename__ = "investigation_jobs"

    id = Column(
        String,
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    goal = Column(Text, nullable=False)

    # Lifecycle: QUEUED → PLANNING → RUNNING → REFLECTING → SUCCESS / FAILED
    status = Column(String, default="QUEUED")

    # Human-readable label for the current execution step.
    # Written by the Celery worker after each tool call.
    # Example: "Step 3 — search_codebase"
    # Null when the job is not actively running.
    current_step = Column(String, nullable=True)

    # Full reasoning trace — list of {type, content, tool_calls} dicts
    trace = Column(JSON, default=list)

    # Final answer produced by the agent
    result = Column(Text, nullable=True)

    created_at = Column(
        DateTime(timezone=True),
        default=datetime.utcnow,
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )


class AuditEvent(Base):
    """Append-only audit event with a hash chain for local tamper evidence."""

    __tablename__ = "audit_events"

    id = Column(
        String,
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    actor = Column(String, nullable=False, default="system")
    action = Column(String, nullable=False)
    resource_type = Column(String, nullable=False)
    resource_id = Column(String, nullable=True)
    request_id = Column(String, nullable=True)
    metadata_json = Column(JSON, default=dict)
    previous_hash = Column(String, nullable=True)
    event_hash = Column(String, nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        default=datetime.utcnow,
    )


class User(Base):
    """Local AutoOps user with a role for route-level RBAC."""

    __tablename__ = "users"

    id = Column(
        String,
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    username = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=False)
    role = Column(String, nullable=False, default="viewer")
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(
        DateTime(timezone=True),
        default=datetime.utcnow,
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )


# ── Pydantic API schemas ───────────────────────────────────────────────────────

class GoalRequest(BaseModel):
    """Body for POST /investigate."""
    goal: str


class JobResponse(BaseModel):
    """
    Body for GET /jobs/{job_id}.

    current_step is included so the frontend can display live progress
    without any additional endpoint — it's just another field on the
    same poll response the frontend already reads every 2 seconds.
    """
    id: str
    goal: str
    status: str
    current_step: Optional[str] = None   # live step label, None when idle
    trace: List[Any] = []
    result: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AuditEventResponse(BaseModel):
    id: str
    actor: str
    action: str
    resource_type: str
    resource_id: Optional[str] = None
    request_id: Optional[str] = None
    metadata_json: dict[str, Any] = {}
    previous_hash: Optional[str] = None
    event_hash: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class UserResponse(BaseModel):
    id: str
    username: str
    role: str
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
