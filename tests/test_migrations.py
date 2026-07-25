from sqlalchemy import create_engine, inspect, text
from sqlalchemy.pool import StaticPool

from api.migrations import SCHEMA_LEDGER_VERSION, ensure_schema


def _sqlite_engine():
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def test_ensure_schema_creates_fresh_schema():
    engine = _sqlite_engine()

    result = ensure_schema(engine)
    columns = {column["name"] for column in inspect(engine).get_columns("investigation_jobs")}

    assert "investigation_jobs" in inspect(engine).get_table_names()
    assert "current_step" in columns
    assert "trace" in columns
    assert "schema_migrations" in inspect(engine).get_table_names()
    assert result.applied == []
    assert "current_step" in result.skipped
    assert SCHEMA_LEDGER_VERSION in result.ledgered


def test_ensure_schema_upgrades_older_jobs_table():
    engine = _sqlite_engine()
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE investigation_jobs (
                id VARCHAR PRIMARY KEY,
                goal TEXT NOT NULL,
                status VARCHAR,
                created_at DATETIME
            )
        """))

    result = ensure_schema(engine)
    columns = {column["name"] for column in inspect(engine).get_columns("investigation_jobs")}

    assert {"current_step", "trace", "result", "updated_at"}.issubset(columns)
    assert set(result.applied) == {"current_step", "trace", "result", "updated_at"}
    assert set(result.ledgered) >= {
        "legacy_investigation_jobs_current_step",
        "legacy_investigation_jobs_trace",
        "legacy_investigation_jobs_result",
        "legacy_investigation_jobs_updated_at",
        SCHEMA_LEDGER_VERSION,
    }


def test_ensure_schema_is_idempotent():
    engine = _sqlite_engine()

    first = ensure_schema(engine)
    second = ensure_schema(engine)

    assert first.applied == []
    assert second.applied == []
    assert second.ledgered == []
    assert {"current_step", "trace", "result", "updated_at"}.issubset(set(second.skipped))
