"""Release-gated migration governance checks for AutoOps."""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sys
from dataclasses import asdict, dataclass
from typing import Any


ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MIGRATIONS_DIR = ROOT / "db" / "migrations"
MANIFEST_FILE = MIGRATIONS_DIR / "manifest.json"
FORBIDDEN_DDL = (
    "DROP TABLE",
    "DROP COLUMN",
    "TRUNCATE",
    "DELETE FROM",
    "ALTER TABLE",
)
REQUIRED_TABLES = {
    "investigation_jobs": {"id", "goal", "status", "current_step", "trace", "result", "created_at", "updated_at"},
    "audit_events": {"id", "actor", "action", "resource_type", "resource_id", "request_id", "metadata_json", "previous_hash", "event_hash", "created_at"},
    "users": {"id", "username", "password_hash", "role", "is_active", "created_at", "updated_at"},
    "schema_migrations": {"version", "description", "checksum", "applied_at"},
}


@dataclass(frozen=True)
class PolicyResult:
    check: str
    ok: bool
    detail: str


def _sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_manifest(manifest_file: pathlib.Path) -> dict[str, Any]:
    if not manifest_file.exists():
        return {"schema_version": None, "migrations": []}
    return json.loads(manifest_file.read_text(encoding="utf-8"))


def _display_path(path: pathlib.Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _migration_id_from_name(path: pathlib.Path) -> str:
    return path.stem


def _created_table_columns(sql: str) -> dict[str, set[str]]:
    tables: dict[str, set[str]] = {}
    for match in re.finditer(r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\((.*?)\);", sql, re.IGNORECASE | re.DOTALL):
        table_name = match.group(1)
        body = match.group(2)
        columns: set[str] = set()
        for raw_line in body.splitlines():
            line = raw_line.strip().rstrip(",")
            if not line or line.upper().startswith(("PRIMARY KEY", "FOREIGN KEY", "CONSTRAINT", "UNIQUE")):
                continue
            columns.add(line.split()[0].strip('"'))
        tables[table_name] = columns
    return tables


def run_migration_policy_checks(
    migrations_dir: pathlib.Path = MIGRATIONS_DIR,
    manifest_file: pathlib.Path = MANIFEST_FILE,
) -> dict[str, Any]:
    manifest = _load_manifest(manifest_file)
    manifest_entries = manifest.get("migrations", [])
    sql_files = sorted(migrations_dir.glob("*.sql"))
    ids = [_migration_id_from_name(path) for path in sql_files]
    manifest_by_path = {entry.get("path"): entry for entry in manifest_entries}
    all_sql = "\n".join(path.read_text(encoding="utf-8") for path in sql_files)
    created_tables: dict[str, set[str]] = {}
    for path in sql_files:
        created_tables.update(_created_table_columns(path.read_text(encoding="utf-8")))

    results: list[PolicyResult] = [
        PolicyResult("migrations_dir_exists", migrations_dir.exists(), _display_path(migrations_dir)),
        PolicyResult("manifest_exists", manifest_file.exists(), _display_path(manifest_file)),
        PolicyResult("manifest_schema_version", manifest.get("schema_version") == "1.0", "manifest schema_version is 1.0"),
        PolicyResult("has_migration_files", len(sql_files) >= 4, f"{len(sql_files)} SQL migrations"),
        PolicyResult("ids_are_sorted", ids == sorted(ids), ",".join(ids)),
        PolicyResult("ids_are_unique", len(ids) == len(set(ids)), ",".join(ids)),
        PolicyResult("manifest_covers_all_files", {path.name for path in sql_files} == set(manifest_by_path), "manifest paths match SQL files"),
    ]

    for index, path in enumerate(sql_files, start=1):
        migration_id = _migration_id_from_name(path)
        expected_prefix = f"{index:04d}_"
        entry = manifest_by_path.get(path.name, {})
        sql = path.read_text(encoding="utf-8")
        upper_sql = sql.upper()
        results.extend([
            PolicyResult(f"{migration_id}_sequential_prefix", migration_id.startswith(expected_prefix), expected_prefix),
            PolicyResult(f"{migration_id}_manifest_id", entry.get("id") == migration_id, migration_id),
            PolicyResult(f"{migration_id}_checksum", entry.get("checksum") == _sha256(path), path.name),
            PolicyResult(f"{migration_id}_has_description", bool(entry.get("description")), str(entry.get("description", ""))),
            PolicyResult(f"{migration_id}_forward_only_header", "-- Direction: forward-only" in sql, path.name),
            PolicyResult(f"{migration_id}_idempotent_create", "CREATE TABLE IF NOT EXISTS" in upper_sql or "CREATE INDEX IF NOT EXISTS" in upper_sql, path.name),
        ])
        for forbidden in FORBIDDEN_DDL:
            results.append(PolicyResult(f"{migration_id}_no_{forbidden.lower().replace(' ', '_')}", forbidden not in upper_sql, forbidden))

    for table_name, required_columns in REQUIRED_TABLES.items():
        columns = created_tables.get(table_name, set())
        results.append(PolicyResult(f"table_{table_name}_created", table_name in created_tables, table_name))
        missing = required_columns - columns
        results.append(PolicyResult(f"table_{table_name}_required_columns", not missing, ",".join(sorted(missing)) or "all required columns"))

    results.extend([
        PolicyResult("ledger_table_in_runtime_migrations", "schema_migrations" in all_sql, "schema_migrations table is versioned"),
        PolicyResult("audit_chain_is_versioned", "previous_hash" in all_sql and "event_hash" in all_sql, "audit hash-chain fields are versioned"),
        PolicyResult("rbac_user_index_is_versioned", "ix_users_username" in all_sql, "users.username index is versioned"),
    ])

    failed = [result for result in results if not result.ok]
    return {
        "ok": not failed,
        "passed": len(results) - len(failed),
        "failed": len(failed),
        "checks": [asdict(result) for result in results],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check AutoOps database migration governance.")
    parser.add_argument("--migrations-dir", type=pathlib.Path, default=MIGRATIONS_DIR)
    parser.add_argument("--manifest", type=pathlib.Path, default=MANIFEST_FILE)
    args = parser.parse_args(argv)
    summary = run_migration_policy_checks(args.migrations_dir, args.manifest)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
