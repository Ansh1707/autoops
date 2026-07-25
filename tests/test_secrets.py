import base64
import os

import pytest

from api import secrets as encrypted_secrets
from scripts import secrets_tool


def test_write_load_and_apply_encrypted_secrets(monkeypatch, tmp_path):
    key = encrypted_secrets.generate_secrets_key()
    path = tmp_path / "secrets.autoops.enc"

    encrypted_secrets.write_encrypted_secrets(
        {
            "JWT_SECRET_KEY": "from-encrypted-file",
            "AUTOOPS_BOOTSTRAP_PASSWORD": "encrypted-password",
        },
        path=path,
        key=key,
    )

    assert b"from-encrypted-file" not in path.read_bytes()
    loaded = encrypted_secrets.load_encrypted_secrets(path=path, key=key)
    assert loaded == {
        "JWT_SECRET_KEY": "from-encrypted-file",
        "AUTOOPS_BOOTSTRAP_PASSWORD": "encrypted-password",
    }

    monkeypatch.setenv("JWT_SECRET_KEY", "already-set")
    applied = encrypted_secrets.apply_encrypted_secrets(path=path, key=key)

    assert os.environ["JWT_SECRET_KEY"] == "already-set"
    assert os.environ["AUTOOPS_BOOTSTRAP_PASSWORD"] == "encrypted-password"
    assert applied == {"AUTOOPS_BOOTSTRAP_PASSWORD": "encrypted-password"}


def test_encrypted_secrets_status_reports_required_missing_file(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOOPS_REQUIRE_ENCRYPTED_SECRETS", "true")
    monkeypatch.setenv("AUTOOPS_SECRETS_FILE", str(tmp_path / "missing.autoops.enc"))
    monkeypatch.delenv("AUTOOPS_SECRETS_KEY", raising=False)

    status = encrypted_secrets.encrypted_secrets_status()

    assert status.required is True
    assert status.configured is True
    assert status.valid is False
    assert "not found" in status.detail


def test_encrypted_secrets_status_reports_valid_file(monkeypatch, tmp_path):
    key = encrypted_secrets.generate_secrets_key()
    path = tmp_path / "secrets.autoops.enc"
    encrypted_secrets.write_encrypted_secrets({"JWT_SECRET_KEY": "secret"}, path=path, key=key)

    monkeypatch.setenv("AUTOOPS_SECRETS_FILE", str(path))
    monkeypatch.setenv("AUTOOPS_SECRETS_KEY", key)

    status = encrypted_secrets.encrypted_secrets_status()

    assert status.valid is True
    assert status.loaded_count == 1
    assert "1 entries" in status.detail


def test_invalid_secret_names_are_rejected(tmp_path):
    key = encrypted_secrets.generate_secrets_key()

    with pytest.raises(ValueError, match="Invalid secret name"):
        encrypted_secrets.write_encrypted_secrets({"bad-name": "value"}, path=tmp_path / "bad.enc", key=key)


def test_secrets_tool_write_and_inspect(capsys, tmp_path):
    key = base64.urlsafe_b64encode(bytes([248]) + (b"\0" * 31)).decode("utf-8")
    assert key.startswith("-")
    path = tmp_path / "tool.autoops.enc"

    assert secrets_tool.main(["write", "--file", str(path), "--key", key, "JWT_SECRET_KEY=tool-secret"]) == 0
    write_output = capsys.readouterr()
    assert "Wrote encrypted secrets" in write_output.out

    assert secrets_tool.main(["inspect", "--file", str(path), "--key", key]) == 0
    inspect_output = capsys.readouterr()
    assert "JWT_SECRET_KEY" in inspect_output.out
    assert "tool-secret" not in inspect_output.out
