from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.migrations import ensure_schema
from api.preflight import run_preflight


class FakeResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def fake_urlopen(url, timeout=2):
    return FakeResponse()


def _db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ensure_schema(engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return Session()


def test_preflight_passes_required_checks(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    inbox = tmp_path / "inbox"
    allowed = tmp_path / "allowed"
    chroma = tmp_path / "chroma"
    gmail_credentials = tmp_path / "Credentials.json"
    inbox.mkdir()
    allowed.mkdir()
    gmail_credentials.write_text("{}", encoding="utf-8")

    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://ollama.local")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-with-at-least-32-bytes")
    monkeypatch.setenv("CHROMA_PERSIST_DIR", str(chroma))
    monkeypatch.setenv("AUTOOPS_INBOX", str(inbox))
    monkeypatch.setenv("AUTOOPS_ALLOWED_ROOTS", str(allowed))
    monkeypatch.setenv("GMAIL_CREDENTIALS_PATH", str(gmail_credentials))
    monkeypatch.setattr(
        "api.preflight.session_memory_status",
        lambda: {"ok": True, "backend": "redis"},
    )
    monkeypatch.setattr(
        "api.preflight.shutil.which",
        lambda name: f"/usr/local/bin/{name}" if name in {"tesseract", "pdftoppm"} else None,
    )

    with _db_session() as db:
        result = run_preflight(db, urlopen=fake_urlopen)

    assert result["ok"] is True
    assert result["required_failed"] == 0
    assert {check["name"] for check in result["checks"]} >= {
        "database:connection",
        "redis:session_memory",
        "path:chroma",
        "path:backups",
        "backup:encryption",
        "secrets:encrypted_file",
        "rate_limit:config",
        "path:inbox",
        "gmail:credentials",
        "ocr:tooling",
        "ollama:api",
    }
    ocr_check = next(check for check in result["checks"] if check["name"] == "ocr:tooling")
    assert ocr_check["ok"] is True
    assert ocr_check["required"] is False
    encryption_check = next(check for check in result["checks"] if check["name"] == "backup:encryption")
    assert encryption_check["ok"] is True
    assert encryption_check["required"] is False
    secrets_check = next(check for check in result["checks"] if check["name"] == "secrets:encrypted_file")
    assert secrets_check["ok"] is True
    assert secrets_check["required"] is False


def test_preflight_requires_backup_key_when_encryption_is_default(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    inbox = tmp_path / "inbox"
    allowed = tmp_path / "allowed"
    inbox.mkdir()
    allowed.mkdir()

    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-with-at-least-32-bytes")
    monkeypatch.setenv("CHROMA_PERSIST_DIR", str(tmp_path / "chroma"))
    monkeypatch.setenv("AUTOOPS_INBOX", str(inbox))
    monkeypatch.setenv("AUTOOPS_ALLOWED_ROOTS", str(allowed))
    monkeypatch.setenv("AUTOOPS_BACKUP_ENCRYPT_BY_DEFAULT", "true")
    monkeypatch.delenv("AUTOOPS_BACKUP_ENCRYPTION_KEY", raising=False)
    monkeypatch.setattr(
        "api.preflight.session_memory_status",
        lambda: {"ok": True, "backend": "redis"},
    )
    monkeypatch.setattr("api.preflight.shutil.which", lambda name: None)

    with _db_session() as db:
        result = run_preflight(db, urlopen=fake_urlopen)

    encryption_check = next(check for check in result["checks"] if check["name"] == "backup:encryption")
    assert result["ok"] is False
    assert encryption_check["ok"] is False
    assert encryption_check["required"] is True
    assert "AUTOOPS_BACKUP_ENCRYPTION_KEY" in encryption_check["detail"]


def test_preflight_marks_missing_gmail_as_optional(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    inbox = tmp_path / "inbox"
    allowed = tmp_path / "allowed"
    inbox.mkdir()
    allowed.mkdir()

    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-with-at-least-32-bytes")
    monkeypatch.setenv("CHROMA_PERSIST_DIR", str(tmp_path / "chroma"))
    monkeypatch.setenv("AUTOOPS_INBOX", str(inbox))
    monkeypatch.setenv("AUTOOPS_ALLOWED_ROOTS", str(allowed))
    monkeypatch.setenv("GMAIL_CREDENTIALS_PATH", str(tmp_path / "missing.json"))
    monkeypatch.setattr(
        "api.preflight.session_memory_status",
        lambda: {"ok": True, "backend": "redis"},
    )
    monkeypatch.setattr("api.preflight.shutil.which", lambda name: None)

    with _db_session() as db:
        result = run_preflight(db, urlopen=fake_urlopen)

    gmail_check = next(check for check in result["checks"] if check["name"] == "gmail:credentials")
    assert result["ok"] is True
    assert gmail_check["ok"] is False
    assert gmail_check["required"] is False
    ocr_check = next(check for check in result["checks"] if check["name"] == "ocr:tooling")
    assert ocr_check["ok"] is False
    assert ocr_check["required"] is False


def test_preflight_fails_when_required_redis_is_down(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    inbox = tmp_path / "inbox"
    allowed = tmp_path / "allowed"
    inbox.mkdir()
    allowed.mkdir()

    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-with-at-least-32-bytes")
    monkeypatch.setenv("CHROMA_PERSIST_DIR", str(tmp_path / "chroma"))
    monkeypatch.setenv("AUTOOPS_INBOX", str(inbox))
    monkeypatch.setenv("AUTOOPS_ALLOWED_ROOTS", str(allowed))
    monkeypatch.setattr(
        "api.preflight.session_memory_status",
        lambda: {"ok": False, "backend": "redis", "error": "redis down"},
    )
    monkeypatch.setattr("api.preflight.shutil.which", lambda name: None)

    with _db_session() as db:
        result = run_preflight(db, urlopen=fake_urlopen)

    assert result["ok"] is False
    assert result["required_failed"] == 1
    redis_check = next(check for check in result["checks"] if check["name"] == "redis:session_memory")
    assert redis_check["detail"] == "redis down"


def test_preflight_requires_encrypted_secrets_when_enabled(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    inbox = tmp_path / "inbox"
    allowed = tmp_path / "allowed"
    inbox.mkdir()
    allowed.mkdir()

    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-with-at-least-32-bytes")
    monkeypatch.setenv("CHROMA_PERSIST_DIR", str(tmp_path / "chroma"))
    monkeypatch.setenv("AUTOOPS_INBOX", str(inbox))
    monkeypatch.setenv("AUTOOPS_ALLOWED_ROOTS", str(allowed))
    monkeypatch.setenv("AUTOOPS_REQUIRE_ENCRYPTED_SECRETS", "true")
    monkeypatch.setenv("AUTOOPS_SECRETS_FILE", str(tmp_path / "missing.autoops.enc"))
    monkeypatch.setattr(
        "api.preflight.session_memory_status",
        lambda: {"ok": True, "backend": "redis"},
    )
    monkeypatch.setattr("api.preflight.shutil.which", lambda name: None)

    with _db_session() as db:
        result = run_preflight(db, urlopen=fake_urlopen)

    secrets_check = next(check for check in result["checks"] if check["name"] == "secrets:encrypted_file")
    assert result["ok"] is False
    assert secrets_check["ok"] is False
    assert secrets_check["required"] is True
    assert "not found" in secrets_check["detail"]
