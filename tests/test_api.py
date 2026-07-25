"""
tests/test_api.py

API test suite. Covers:
  1. Unauthorized access is rejected (401/403)
  2. POST /token returns a real signed JWT
  3. Authenticated job creation returns QUEUED + job_id
  4. GET /jobs/{id} returns current_step in the JobResponse schema
  5. GET /health returns ok

Run with:
    pytest tests/ -v
"""

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.auth import create_access_token, hash_password
from api import main as api_main
from api.models import AuditEvent, Base, InvestigationJob, User
from api.rate_limit import reset_rate_limits
from api.status import JobStatus


test_engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
Base.metadata.create_all(bind=test_engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


api_main.app.dependency_overrides[api_main.get_db] = override_get_db
client = TestClient(api_main.app)


def auth_headers(role: str = "owner", username: str = "admin_user") -> dict:
    valid_token = create_access_token(data={'sub': username, 'username': username, 'role': role})
    return {'Authorization': f'Bearer {valid_token}'}


@pytest.fixture(autouse=True)
def clean_db_and_stub_worker(monkeypatch):
    Base.metadata.drop_all(bind=test_engine)
    api_main.ensure_schema(test_engine)
    reset_rate_limits()
    api_main.metrics_registry.reset()
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-with-at-least-32-bytes")
    monkeypatch.setenv("AUTOOPS_ENV", "test")
    monkeypatch.delenv("AUTOOPS_RATE_LIMIT_REQUESTS_PER_MINUTE", raising=False)
    monkeypatch.delenv("AUTOOPS_RATE_LIMIT_JOB_SUBMITS_PER_MINUTE", raising=False)
    monkeypatch.delenv("AUTOOPS_RATE_LIMIT_WINDOW_SECONDS", raising=False)
    monkeypatch.delenv("AUTOOPS_RATE_LIMIT_ENABLED", raising=False)

    queued_tasks = []

    def fake_enqueue(goal, job_id):
        queued_tasks.append({"args": [goal], "task_id": job_id})
        return None

    monkeypatch.setattr(api_main, "enqueue_investigation", fake_enqueue)
    yield queued_tasks


# ── 1. Unauthorized access ─────────────────────────────────────────────────────

def test_unauthorized_access():
    """POST /investigate without a token must return 401 or 403."""
    response = client.post('/investigate', json={'goal': 'Check metrics'})
    assert response.status_code in (401, 403), (
        f'Expected 401 or 403, got {response.status_code}'
    )
    assert 'detail' in response.json()


# ── 2. Token endpoint ──────────────────────────────────────────────────────────

def test_token_endpoint_returns_jwt():
    """POST /token with valid credentials must return a signed JWT."""
    response = client.post('/token', json={'username': 'admin', 'password': 'password'})
    assert response.status_code == 200, f'Expected 200, got {response.status_code}'
    data = response.json()
    assert 'access_token' in data, 'Response missing access_token'
    assert data.get('token_type') == 'bearer'
    assert data.get('role') == 'owner'
    # The token must be a proper JWT (three dot-separated segments)
    parts = data['access_token'].split('.')
    assert len(parts) == 3, 'access_token is not a valid JWT (expected 3 segments)'

    with TestingSessionLocal() as db:
        user = db.query(User).filter(User.username == "admin").first()
        assert user is not None
        assert user.role == "owner"
        assert user.password_hash != "password"
        assert user.password_hash.startswith("pbkdf2_sha256$")


def test_token_endpoint_rejects_bad_credentials():
    """POST /token with wrong password must return 401."""
    response = client.post('/token', json={'username': 'admin', 'password': 'wrong'})
    assert response.status_code == 401


def test_version_endpoint_returns_release_metadata():
    response = client.get("/version")

    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "autoops"
    assert data["version"]
    assert data["environment"] == "test"
    assert "build_sha" in data


def test_login_uses_database_user_role():
    with TestingSessionLocal() as db:
        db.add(User(username="viewer", password_hash=hash_password("viewer-pass"), role="viewer"))
        db.commit()

    response = client.post('/token', json={'username': 'viewer', 'password': 'viewer-pass'})

    assert response.status_code == 200
    assert response.json()["role"] == "viewer"


# ── 3. Authenticated job creation ─────────────────────────────────────────────

def test_authorized_investigation_creation(clean_db_and_stub_worker):
    """Authenticated POST /investigate must create a QUEUED job."""
    headers = auth_headers("operator")
    payload = {'goal': 'Check the checkout-service metrics'}

    response = client.post('/investigate', json=payload, headers=headers)
    assert response.status_code == 200, f'Expected 200, got {response.status_code}'

    data = response.json()
    assert 'job_id' in data, 'Response missing job_id'
    assert data['status'] == JobStatus.QUEUED.value, f"Expected QUEUED, got {data['status']}"
    assert clean_db_and_stub_worker == [
        {"args": ["Check the checkout-service metrics"], "task_id": data["job_id"]}
    ]


def test_viewer_cannot_submit_investigation(clean_db_and_stub_worker):
    response = client.post(
        '/investigate',
        json={'goal': 'Should not run'},
        headers=auth_headers("viewer", username="viewer_user"),
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Insufficient permissions"
    assert clean_db_and_stub_worker == []


def test_users_me_returns_current_role():
    with TestingSessionLocal() as db:
        db.add(User(username="operator", password_hash=hash_password("op-pass"), role="operator"))
        db.commit()

    login_response = client.post('/token', json={'username': 'operator', 'password': 'op-pass'})
    token = login_response.json()["access_token"]

    response = client.get("/users/me", headers={'Authorization': f'Bearer {token}'})

    assert response.status_code == 200
    assert response.json()["username"] == "operator"
    assert response.json()["role"] == "operator"


def test_admin_can_create_and_list_users():
    create_response = client.post(
        "/users",
        json={"username": "new-viewer", "password": "viewer-pass", "role": "viewer"},
        headers=auth_headers("admin"),
    )

    assert create_response.status_code == 200
    assert create_response.json()["username"] == "new-viewer"
    assert create_response.json()["role"] == "viewer"
    assert "password_hash" not in create_response.json()

    list_response = client.get("/users", headers=auth_headers("admin"))
    assert list_response.status_code == 200
    assert [user["username"] for user in list_response.json()] == ["new-viewer"]


def test_viewer_cannot_create_or_list_users():
    list_response = client.get("/users", headers=auth_headers("viewer", username="viewer_user"))
    create_response = client.post(
        "/users",
        json={"username": "blocked", "password": "blocked-pass", "role": "viewer"},
        headers=auth_headers("viewer", username="viewer_user"),
    )

    assert list_response.status_code == 403
    assert create_response.status_code == 403


# ── 4. JobResponse schema includes current_step ────────────────────────────────

def test_job_response_includes_current_step():
    """
    GET /jobs/{id} must return current_step in the response body.

    current_step is None for a freshly-created QUEUED job.
    This test verifies the field is present in the schema (not a KeyError),
    which confirms the new column is wired end-to-end through the model,
    Pydantic schema, and API endpoint.
    """
    headers = auth_headers("operator")

    # Create a job
    create_res = client.post(
        '/investigate',
        json={'goal': 'Schema field check'},
        headers=headers,
    )
    assert create_res.status_code == 200
    job_id = create_res.json()['job_id']

    # Fetch it back
    get_res = client.get(f'/jobs/{job_id}', headers=headers)
    assert get_res.status_code == 200

    data = get_res.json()
    # current_step must be present in the response (may be None)
    assert 'current_step' in data, (
        'JobResponse schema is missing current_step field. '
        'Check api/models.py JobResponse and the DB column migration.'
    )
    # For a QUEUED job it should be None
    assert data['current_step'] is None, (
        f"Expected current_step=None for a QUEUED job, got: {data['current_step']}"
    )


def test_jobs_endpoint_lists_recent_jobs():
    with TestingSessionLocal() as db:
        db.add_all([
            InvestigationJob(goal="old", status=JobStatus.SUCCESS.value),
            InvestigationJob(goal="active", status=JobStatus.RUNNING.value, current_step="checking"),
            InvestigationJob(goal="new", status=JobStatus.FAILED.value, result="failed safely"),
        ])
        db.commit()

    response = client.get("/jobs?limit=2", headers=auth_headers("viewer", username="viewer_user"))

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert {job["goal"] for job in data}.issubset({"old", "active", "new"})
    assert {"id", "goal", "status", "current_step", "trace", "result", "created_at", "updated_at"}.issubset(data[0])


# ── 5. Health check ────────────────────────────────────────────────────────────

def test_health_endpoint():
    """GET /health must return 200 with status ok (no auth required)."""
    response = client.get('/health')
    assert response.status_code == 200
    assert response.json().get('status') == 'ok'
    assert response.headers.get("X-Request-ID")


def test_request_id_header_is_preserved():
    response = client.get("/health", headers={"X-Request-ID": "test-request-id"})

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "test-request-id"


def test_readiness_endpoint():
    """GET /ready must verify DB access."""
    response = client.get('/ready')
    assert response.status_code == 200
    assert response.json().get('status') == 'ready'


def test_metrics_endpoint_returns_job_counts():
    with TestingSessionLocal() as db:
        db.add_all([
            InvestigationJob(goal="one", status=JobStatus.QUEUED.value),
            InvestigationJob(goal="two", status=JobStatus.SUCCESS.value),
            InvestigationJob(goal="three", status=JobStatus.FAILED.value),
        ])
        db.commit()

    client.get("/health")
    response = client.get("/metrics")

    assert response.status_code == 200
    data = response.json()
    assert data["jobs_total"] == 3
    assert data["jobs_active"] == 1
    assert data["jobs_terminal"] == 2
    assert data["audit_events_total"] == 0
    assert data["jobs_by_status"] == {
        JobStatus.QUEUED.value: 1,
        JobStatus.SUCCESS.value: 1,
        JobStatus.FAILED.value: 1,
    }
    assert data["runtime"]["process_uptime_seconds"] >= 0
    assert any(key.startswith("autoops_api_requests_total") for key in data["runtime"]["counters"])


def test_prometheus_metrics_endpoint_returns_text():
    with TestingSessionLocal() as db:
        db.add(InvestigationJob(goal="one", status=JobStatus.SUCCESS.value))
        db.commit()

    client.get("/health")
    response = client.get("/metrics/prometheus")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    text = response.text
    assert "autoops_process_uptime_seconds" in text
    assert 'autoops_jobs_total{status="SUCCESS"} 1' in text
    assert "autoops_api_requests_total" in text
    assert "autoops_api_request_duration_seconds_count" in text


def test_slo_endpoint_reports_healthy_status():
    with TestingSessionLocal() as db:
        db.add(InvestigationJob(goal="one", status=JobStatus.SUCCESS.value))
        db.commit()

    response = client.get("/slo")

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert {objective["name"] for objective in data["objectives"]} == {
        "active_job_backlog",
        "failed_job_ratio",
        "api_request_latency_p95_ms",
    }


def test_slo_endpoint_reports_failed_status(monkeypatch):
    monkeypatch.setenv("AUTOOPS_SLO_MAX_ACTIVE_JOBS", "1")
    with TestingSessionLocal() as db:
        db.add_all([
            InvestigationJob(goal="one", status=JobStatus.RUNNING.value),
            InvestigationJob(goal="two", status=JobStatus.QUEUED.value),
        ])
        db.commit()

    response = client.get("/slo")

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is False
    assert any(
        objective["name"] == "active_job_backlog" and not objective["ok"]
        for objective in data["objectives"]
    )


def test_preflight_endpoint(monkeypatch):
    monkeypatch.setattr(
        api_main,
        "run_preflight",
        lambda db: {
            "ok": True,
            "required_failed": 0,
            "optional_failed": 0,
            "checks": [],
        },
    )

    response = client.get("/preflight")

    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_global_rate_limit_returns_429(monkeypatch):
    monkeypatch.setenv("AUTOOPS_RATE_LIMIT_REQUESTS_PER_MINUTE", "1")
    monkeypatch.setenv("AUTOOPS_RATE_LIMIT_WINDOW_SECONDS", "60")
    reset_rate_limits()

    first = client.get("/health")
    second = client.get("/health")

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.headers["Retry-After"]
    assert second.headers["X-RateLimit-Limit"] == "1"
    assert second.headers["X-Request-ID"]
    counters = api_main.metrics_registry.snapshot()["counters"]
    assert any(key.startswith("autoops_api_rate_limit_blocks_total") for key in counters)


def test_investigation_submit_rate_limit_blocks_enqueue(clean_db_and_stub_worker, monkeypatch):
    monkeypatch.setenv("AUTOOPS_RATE_LIMIT_JOB_SUBMITS_PER_MINUTE", "1")
    monkeypatch.setenv("AUTOOPS_RATE_LIMIT_WINDOW_SECONDS", "60")
    reset_rate_limits()
    headers = auth_headers("operator")

    first = client.post('/investigate', json={'goal': 'first job'}, headers=headers)
    second = client.post('/investigate', json={'goal': 'second job'}, headers=headers)

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["detail"] == "Rate limit exceeded for investigation submissions."
    assert len(clean_db_and_stub_worker) == 1


def test_backup_endpoints_are_authenticated(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOOPS_BACKUP_DIR", str(tmp_path / "backups"))
    headers = auth_headers("operator")

    create_response = client.post("/backups", json={"include_files": False}, headers=headers)
    assert create_response.status_code == 200
    backup_id = create_response.json()["backup_id"]

    list_response = client.get("/backups", headers=headers)
    assert list_response.status_code == 200
    assert list_response.json()["backups"][0]["backup_id"] == backup_id

    inspect_response = client.get(f"/backups/{backup_id}", headers=headers)
    assert inspect_response.status_code == 200
    assert inspect_response.json()["manifest"]["include_files"] is False

    restore_response = client.post(f"/backups/{backup_id}/restore", json={"dry_run": True}, headers=auth_headers("admin"))
    assert restore_response.status_code == 200
    assert restore_response.json()["dry_run"] is True


def test_backup_real_restore_endpoint_requires_enable(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOOPS_BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.delenv("AUTOOPS_ENABLE_RESTORE", raising=False)
    headers = auth_headers("admin")

    create_response = client.post("/backups", json={"include_files": False}, headers=headers)
    backup_id = create_response.json()["backup_id"]

    restore_response = client.post(f"/backups/{backup_id}/restore", json={"dry_run": False}, headers=headers)
    assert restore_response.status_code == 403
    assert "AUTOOPS_ENABLE_RESTORE" in restore_response.json()["detail"]


def test_backup_endpoint_can_create_encrypted_backup(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOOPS_BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setenv("AUTOOPS_BACKUP_ENCRYPTION_KEY", Fernet.generate_key().decode("utf-8"))
    headers = auth_headers("operator")

    create_response = client.post("/backups", json={"include_files": False, "encrypt": True}, headers=headers)
    assert create_response.status_code == 200
    data = create_response.json()
    assert data["backup_id"].endswith(".zip.enc")
    assert data["encrypted"] is True

    inspect_response = client.get(f"/backups/{data['backup_id']}", headers=headers)
    assert inspect_response.status_code == 200
    assert inspect_response.json()["encrypted"] is True


def test_backup_endpoint_rejects_encryption_without_key(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOOPS_BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.delenv("AUTOOPS_BACKUP_ENCRYPTION_KEY", raising=False)
    headers = auth_headers("operator")

    create_response = client.post("/backups", json={"include_files": False, "encrypt": True}, headers=headers)
    assert create_response.status_code == 403
    assert "AUTOOPS_BACKUP_ENCRYPTION_KEY" in create_response.json()["detail"]


def test_audit_endpoint_records_sensitive_actions(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOOPS_BACKUP_DIR", str(tmp_path / "backups"))
    headers = {**auth_headers("operator"), 'X-Request-ID': 'audit-test'}

    create_response = client.post("/backups", json={"include_files": False}, headers=headers)
    assert create_response.status_code == 200

    audit_response = client.get("/audit", headers=headers)
    assert audit_response.status_code == 200
    events = audit_response.json()

    assert events[0]["action"] == "backup.create"
    assert events[0]["actor"] == "admin_user"
    assert events[0]["request_id"] == "audit-test"
    assert len(events[0]["event_hash"]) == 64


def test_audit_verify_endpoint_detects_tampered_chain():
    headers = auth_headers("viewer")
    with TestingSessionLocal() as db:
        db.add(
            AuditEvent(
                actor="system",
                action="seed",
                resource_type="test",
                event_hash="0" * 64,
            )
        )
        db.commit()

    response = client.get("/audit/verify", headers=headers)

    assert response.status_code == 200
    assert response.json()["ok"] is False
