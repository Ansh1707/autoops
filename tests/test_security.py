import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agent.tool_domains.shell import validate_command
from api import auth
from api.main import cors_origins
from api.migrations import ensure_schema


def test_production_requires_non_default_jwt_secret(monkeypatch):
    monkeypatch.setenv("AUTOOPS_ENV", "production")
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)

    with pytest.raises(RuntimeError, match="JWT_SECRET_KEY"):
        auth.create_access_token({"sub": "admin"})


def test_production_requires_non_default_bootstrap_password(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ensure_schema(engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setenv("AUTOOPS_ENV", "production")
    monkeypatch.delenv("AUTOOPS_BOOTSTRAP_PASSWORD", raising=False)

    with Session() as db:
        with pytest.raises(RuntimeError, match="AUTOOPS_BOOTSTRAP_PASSWORD"):
            auth.bootstrap_default_user(db)


def test_mock_token_is_disabled_by_default_in_production(monkeypatch):
    monkeypatch.setenv("AUTOOPS_ENV", "production")
    monkeypatch.setenv("JWT_SECRET_KEY", "real-production-secret")
    monkeypatch.delenv("AUTOOPS_ALLOW_MOCK_TOKEN", raising=False)

    credentials = HTTPAuthorizationCredentials(
        scheme="Bearer",
        credentials=auth.MOCK_TOKEN,
    )

    with pytest.raises(HTTPException) as exc_info:
        auth.verify_token(credentials)
    assert exc_info.value.status_code == 401


def test_cors_rejects_wildcard_in_production(monkeypatch):
    monkeypatch.setenv("AUTOOPS_ENV", "production")
    monkeypatch.setenv("AUTOOPS_CORS_ORIGINS", "*")

    with pytest.raises(RuntimeError, match="Wildcard CORS"):
        cors_origins()


def test_cors_parses_explicit_origins(monkeypatch):
    monkeypatch.setenv("AUTOOPS_ENV", "development")
    monkeypatch.setenv("AUTOOPS_CORS_ORIGINS", "http://localhost:5173, https://autoops.local")

    assert cors_origins() == ["http://localhost:5173", "https://autoops.local"]


def test_shell_blocks_paths_outside_allowed_roots(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOOPS_ALLOWED_ROOTS", str(tmp_path))

    error = validate_command(["cat", "/etc/passwd"])

    assert error is not None
    assert "outside allowed roots" in error


def test_shell_allows_paths_inside_allowed_roots(monkeypatch, tmp_path):
    allowed_file = tmp_path / "notes.txt"
    allowed_file.write_text("safe", encoding="utf-8")
    monkeypatch.setenv("AUTOOPS_ALLOWED_ROOTS", str(tmp_path))

    assert validate_command(["cat", str(allowed_file)]) is None


def test_shell_blocks_control_operators():
    assert validate_command(["ls", ";", "pwd"]) == (
        "Shell control operators are not allowed. Run one simple command at a time."
    )


def test_shell_restricts_docker_to_read_only_subcommands():
    error = validate_command(["docker", "rm", "container-id"])

    assert error is not None
    assert "not allowed" in error
    assert validate_command(["docker", "ps"]) is None
