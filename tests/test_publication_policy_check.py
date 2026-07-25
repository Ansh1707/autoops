from scripts import publication_policy_check


def test_publication_policy_current_repository_passes():
    summary = publication_policy_check.run_publication_policy_checks()

    assert summary["ok"] is True
    assert summary["failed"] == 0
    assert {check["check"] for check in summary["checks"]} >= {
        "git_private_directory_rules",
        "docker_private_directory_rules",
        "git_secret_rules",
        "docker_secret_rules",
    }


def test_publication_policy_rejects_missing_private_data_controls(tmp_path):
    gitignore = tmp_path / ".gitignore"
    dockerignore = tmp_path / ".dockerignore"
    gitignore.write_text(".env\n", encoding="utf-8")
    dockerignore.write_text(".env\n", encoding="utf-8")

    summary = publication_policy_check.run_publication_policy_checks(
        gitignore,
        dockerignore,
        tmp_path,
    )
    failed = {check["check"] for check in summary["checks"] if not check["ok"]}

    assert summary["ok"] is False
    assert "git_private_directory_rules" in failed
    assert "docker_private_directory_rules" in failed
    assert "private_directory_placeholders" in failed
    assert "git_secret_rules" in failed
    assert "docker_secret_rules" in failed
