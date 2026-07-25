import json

from scripts import release_manifest


def test_release_manifest_contains_provenance_and_policy_summaries():
    manifest = release_manifest.build_release_manifest()

    assert manifest["ok"] is True
    assert manifest["project"] == "autoops"
    assert manifest["version"]
    assert "release_manifest" in manifest["release_gate_checks"]
    assert manifest["policy_summaries"]["container_security"]["ok"] is True
    assert manifest["policy_summaries"]["secret_scanning_policy"]["ok"] is True
    assert manifest["policy_summaries"]["publication_policy"]["ok"] is True
    assert manifest["policy_summaries"]["vulnerability_policy"]["ok"] is True
    assert manifest["policy_summaries"]["signing_policy"]["ok"] is True
    assert manifest["policy_summaries"]["deployment_policy"]["ok"] is True
    assert manifest["policy_summaries"]["frontend_dashboard_policy"]["ok"] is True
    assert manifest["policy_summaries"]["api_contract_policy"]["ok"] is True
    assert manifest["policy_summaries"]["performance_policy"]["ok"] is True
    assert manifest["policy_summaries"]["migration_policy"]["ok"] is True
    assert manifest["policy_summaries"]["resilience_policy"]["ok"] is True
    assert manifest["policy_summaries"]["dependency_policy"]["sbom_summary"]["total_components"] > 0
    assert manifest["missing_artifacts"] == []
    assert all(
        artifact["sha256"] and len(artifact["sha256"]) == 64
        for artifact in manifest["source_artifacts"]
    )


def test_release_manifest_cli_writes_json_file(tmp_path):
    output = tmp_path / "release-manifest.json"

    assert release_manifest.main(["--output", str(output)]) == 0

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["ok"] is True
    assert payload["project"] == "autoops"
