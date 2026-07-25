import json
import shutil

from scripts import migration_policy_check


def test_migration_policy_current_files_pass():
    summary = migration_policy_check.run_migration_policy_checks()

    assert summary["ok"] is True
    assert summary["failed"] == 0
    checks = {check["check"] for check in summary["checks"]}
    assert "table_schema_migrations_created" in checks
    assert "ledger_table_in_runtime_migrations" in checks


def test_migration_policy_rejects_checksum_drift(tmp_path):
    migrations_dir = tmp_path / "migrations"
    shutil.copytree(migration_policy_check.MIGRATIONS_DIR, migrations_dir)
    manifest_file = migrations_dir / "manifest.json"
    target = migrations_dir / "0001_initial_jobs.sql"
    target.write_text(target.read_text(encoding="utf-8") + "\n-- accidental edit\n", encoding="utf-8")

    summary = migration_policy_check.run_migration_policy_checks(migrations_dir, manifest_file)

    assert summary["ok"] is False
    failed = {check["check"] for check in summary["checks"] if not check["ok"]}
    assert "0001_initial_jobs_checksum" in failed


def test_migration_policy_rejects_destructive_ddl(tmp_path):
    migrations_dir = tmp_path / "migrations"
    shutil.copytree(migration_policy_check.MIGRATIONS_DIR, migrations_dir)
    destructive = migrations_dir / "0005_drop_jobs.sql"
    destructive.write_text(
        "-- AutoOps migration: 0005_drop_jobs\n"
        "-- Description: Bad destructive migration.\n"
        "-- Direction: forward-only\n\n"
        "DROP TABLE investigation_jobs;\n",
        encoding="utf-8",
    )
    manifest = json.loads((migrations_dir / "manifest.json").read_text(encoding="utf-8"))
    manifest["migrations"].append({
        "id": "0005_drop_jobs",
        "path": "0005_drop_jobs.sql",
        "description": "Bad destructive migration.",
        "checksum": migration_policy_check._sha256(destructive),
    })
    (migrations_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    summary = migration_policy_check.run_migration_policy_checks(migrations_dir, migrations_dir / "manifest.json")

    assert summary["ok"] is False
    failed = {check["check"] for check in summary["checks"] if not check["ok"]}
    assert "0005_drop_jobs_no_drop_table" in failed
