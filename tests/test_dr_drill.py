from scripts import dr_drill


def test_dr_drill_plain_backup_restore_passes(tmp_path):
    summary = dr_drill.run_dr_drill(workdir=tmp_path)

    assert summary["ok"] is True
    assert summary["failed"] == 0
    assert summary["encrypted"] is False
    assert {check["name"] for check in summary["checks"]} == {
        "backup_created",
        "backup_checksum",
        "manifest_counts",
        "file_payload_present",
        "dry_run_restore_counts",
        "real_restore_isolated_target",
    }


def test_dr_drill_encrypted_backup_restore_passes(tmp_path):
    summary = dr_drill.run_dr_drill(workdir=tmp_path, encrypt=True)

    assert summary["ok"] is True
    assert summary["failed"] == 0
    assert summary["encrypted"] is True
