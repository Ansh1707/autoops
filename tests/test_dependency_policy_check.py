import json

import pytest

from scripts import dependency_policy_check


def test_dependency_policy_current_project_passes():
    summary = dependency_policy_check.run_dependency_policy_checks()

    assert summary["ok"] is True
    assert summary["failed"] == 0
    assert summary["sbom_summary"]["python_direct_dependencies"] > 0
    assert summary["sbom_summary"]["npm_locked_dependencies"] > 0


def test_parse_requirements_rejects_loose_specs(tmp_path):
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("fastapi>=0.104.1\n", encoding="utf-8")

    with pytest.raises(ValueError, match="exact NAME==VERSION"):
        dependency_policy_check.parse_requirements(requirements)


def test_frontend_dependency_exactness_helper_rejects_ranges():
    assert dependency_policy_check._is_exact_npm_spec("1.2.3") is True
    assert dependency_policy_check._is_exact_npm_spec("^1.2.3") is False
    assert dependency_policy_check._is_exact_npm_spec("~1.2.3") is False
    assert dependency_policy_check._is_exact_npm_spec(">=1.2.3") is False


def test_build_sbom_contains_python_and_npm_components():
    sbom = dependency_policy_check.build_sbom()

    component_names = {component["name"] for component in sbom["components"]}
    assert sbom["bomFormat"] == "CycloneDX"
    assert "fastapi" in component_names
    assert "react" in component_names


def test_lock_root_mismatch_is_reported(monkeypatch, tmp_path):
    package_json = tmp_path / "package.json"
    package_lock = tmp_path / "package-lock.json"
    package_json.write_text(
        json.dumps({"dependencies": {"react": "18.3.1"}, "devDependencies": {}}),
        encoding="utf-8",
    )
    package_lock.write_text(
        json.dumps(
            {
                "packages": {
                    "": {"dependencies": {"react": "^18.3.1"}},
                    "node_modules/react": {"version": "18.3.1"},
                }
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(dependency_policy_check, "PACKAGE_JSON", package_json)
    monkeypatch.setattr(dependency_policy_check, "PACKAGE_LOCK", package_lock)

    results = dependency_policy_check._check_frontend_dependencies()
    lock_check = next(result for result in results if result.check == "frontend_lock_matches_package_json")

    assert lock_check.ok is False
    assert "package.json=18.3.1" in lock_check.detail
