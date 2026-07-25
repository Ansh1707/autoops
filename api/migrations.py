from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Engine, inspect, text

from api.models import Base


@dataclass(frozen=True)
class MigrationResult:
    applied: list[str]
    skipped: list[str]
    ledgered: list[str]


SCHEMA_LEDGER_VERSION = "runtime_schema_20260705"
SCHEMA_LEDGER_CHECKSUM = "autoops-runtime-schema-ledger-v1"
SCHEMA_MIGRATIONS_TABLE = "schema_migrations"
OPTIONAL_JOB_COLUMNS = {
    "current_step": {
        "postgresql": "ALTER TABLE investigation_jobs ADD COLUMN IF NOT EXISTS current_step VARCHAR",
        "sqlite": "ALTER TABLE investigation_jobs ADD COLUMN current_step VARCHAR",
    },
    "trace": {
        "postgresql": "ALTER TABLE investigation_jobs ADD COLUMN IF NOT EXISTS trace JSON DEFAULT '[]'::json",
        "sqlite": "ALTER TABLE investigation_jobs ADD COLUMN trace JSON DEFAULT '[]'",
    },
    "result": {
        "postgresql": "ALTER TABLE investigation_jobs ADD COLUMN IF NOT EXISTS result TEXT",
        "sqlite": "ALTER TABLE investigation_jobs ADD COLUMN result TEXT",
    },
    "updated_at": {
        "postgresql": "ALTER TABLE investigation_jobs ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP",
        "sqlite": "ALTER TABLE investigation_jobs ADD COLUMN updated_at DATETIME",
    },
}


def _ensure_migration_ledger(engine: Engine) -> None:
    ddl = """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version VARCHAR PRIMARY KEY,
            description VARCHAR NOT NULL,
            checksum VARCHAR NOT NULL,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """
    with engine.begin() as connection:
        connection.execute(text(ddl))


def _record_migration(engine: Engine, version: str, description: str, checksum: str) -> bool:
    existing = _applied_migrations(engine)
    if version in existing:
        return False
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO schema_migrations (version, description, checksum)
                VALUES (:version, :description, :checksum)
                """
            ),
            {"version": version, "description": description, "checksum": checksum},
        )
    return True


def _applied_migrations(engine: Engine) -> set[str]:
    inspector = inspect(engine)
    if not inspector.has_table(SCHEMA_MIGRATIONS_TABLE):
        return set()
    with engine.begin() as connection:
        rows = connection.execute(text("SELECT version FROM schema_migrations")).fetchall()
    return {row[0] for row in rows}


def _column_names(engine: Engine, table_name: str) -> set[str]:
    inspector = inspect(engine)
    if not inspector.has_table(table_name):
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def _ddl_for(engine: Engine, column_name: str) -> str:
    dialect = engine.dialect.name
    ddl_by_dialect = OPTIONAL_JOB_COLUMNS[column_name]
    if dialect in ddl_by_dialect:
        return ddl_by_dialect[dialect]
    return ddl_by_dialect["postgresql"].replace(" IF NOT EXISTS", "")


def ensure_schema(engine: Engine) -> MigrationResult:
    """
    Create current tables and apply small idempotent upgrades for older local DBs.

    This intentionally stays lightweight instead of pulling in Alembic yet. It
    handles the current AutoOps upgrade path: existing investigation_jobs tables
    that predate current_step/result/trace/updated_at.
    """
    Base.metadata.create_all(bind=engine)
    _ensure_migration_ledger(engine)

    applied: list[str] = []
    skipped: list[str] = []
    ledgered: list[str] = []
    existing_columns = _column_names(engine, "investigation_jobs")

    for column_name in OPTIONAL_JOB_COLUMNS:
        if column_name in existing_columns:
            skipped.append(column_name)
            continue

        with engine.begin() as connection:
            connection.execute(text(_ddl_for(engine, column_name)))
        applied.append(column_name)
        ledger_version = f"legacy_investigation_jobs_{column_name}"
        if _record_migration(
            engine,
            ledger_version,
            f"Added legacy investigation_jobs.{column_name} column",
            OPTIONAL_JOB_COLUMNS[column_name]["postgresql"],
        ):
            ledgered.append(ledger_version)
        existing_columns.add(column_name)

    if _record_migration(
        engine,
        SCHEMA_LEDGER_VERSION,
        "Current AutoOps runtime schema is present",
        SCHEMA_LEDGER_CHECKSUM,
    ):
        ledgered.append(SCHEMA_LEDGER_VERSION)

    return MigrationResult(applied=applied, skipped=skipped, ledgered=ledgered)
