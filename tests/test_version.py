from api import version


def test_read_version_prefers_environment(monkeypatch):
    monkeypatch.setenv("AUTOOPS_VERSION", "test-version")

    assert version.read_version() == "test-version"


def test_read_version_falls_back_to_version_file(monkeypatch, tmp_path):
    monkeypatch.delenv("AUTOOPS_VERSION", raising=False)
    version_file = tmp_path / "VERSION"
    version_file.write_text("1.2.3\n", encoding="utf-8")

    assert version.read_version(version_file) == "1.2.3"


def test_build_metadata_is_safe_and_complete(monkeypatch):
    monkeypatch.setenv("AUTOOPS_VERSION", "metadata-test")
    monkeypatch.setenv("AUTOOPS_ENV", "test")
    monkeypatch.setenv("AUTOOPS_BUILD_SHA", "abc123")
    monkeypatch.setenv("JWT_SECRET_KEY", "must-not-appear")

    metadata = version.build_metadata()

    assert metadata["name"] == "autoops"
    assert metadata["version"] == "metadata-test"
    assert metadata["environment"] == "test"
    assert metadata["build_sha"] == "abc123"
    assert "reported_at" in metadata
    assert "must-not-appear" not in str(metadata)
