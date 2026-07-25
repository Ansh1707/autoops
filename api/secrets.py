"""Encrypted local secrets support for AutoOps.

This module intentionally stays small: local development can keep using normal
environment variables, while stricter deployments can load secrets from a
Fernet-encrypted JSON file before the rest of the app reads configuration.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
from dataclasses import dataclass
from typing import Mapping

from cryptography.fernet import Fernet, InvalidToken


DEFAULT_SECRETS_FILE = "secrets.autoops.enc"
TRUE_VALUES = {"1", "true", "yes", "on"}
SECRET_ENV_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")


@dataclass(frozen=True)
class EncryptedSecretsStatus:
    configured: bool
    required: bool
    valid: bool
    loaded_count: int
    path: str
    detail: str


def generate_secrets_key() -> str:
    """Return a URL-safe Fernet key for AUTOOPS_SECRETS_KEY."""
    return Fernet.generate_key().decode("utf-8")


def encrypted_secrets_required() -> bool:
    return os.getenv("AUTOOPS_REQUIRE_ENCRYPTED_SECRETS", "false").lower() in TRUE_VALUES


def encrypted_secrets_path(path: str | pathlib.Path | None = None) -> pathlib.Path:
    raw_path = path or os.getenv("AUTOOPS_SECRETS_FILE", DEFAULT_SECRETS_FILE)
    return pathlib.Path(raw_path).expanduser()


def _configured(path: pathlib.Path) -> bool:
    return bool(os.getenv("AUTOOPS_SECRETS_FILE")) or path.exists()


def _fernet(key: str | None = None) -> Fernet:
    secret_key = (key or os.getenv("AUTOOPS_SECRETS_KEY", "")).strip()
    if not secret_key:
        raise ValueError("AUTOOPS_SECRETS_KEY is required to decrypt encrypted secrets.")
    try:
        return Fernet(secret_key.encode("utf-8"))
    except Exception as exc:
        raise ValueError(f"Invalid AUTOOPS_SECRETS_KEY: {exc}") from exc


def _validate_secret_payload(payload: object) -> dict[str, str]:
    if not isinstance(payload, dict):
        raise ValueError("Encrypted secrets payload must be a JSON object.")

    secrets: dict[str, str] = {}
    for key, value in payload.items():
        if not isinstance(key, str) or not SECRET_ENV_PATTERN.match(key):
            raise ValueError(f"Invalid secret name: {key!r}. Use uppercase environment variable names.")
        if not isinstance(value, str):
            raise ValueError(f"Secret {key} must be a string value.")
        secrets[key] = value
    return secrets


def load_encrypted_secrets(
    path: str | pathlib.Path | None = None,
    key: str | None = None,
) -> dict[str, str]:
    """Decrypt and validate the configured encrypted secrets file."""
    secrets_path = encrypted_secrets_path(path)
    if not secrets_path.exists():
        if encrypted_secrets_required() or os.getenv("AUTOOPS_SECRETS_FILE"):
            raise FileNotFoundError(f"Encrypted secrets file not found: {secrets_path}")
        return {}

    encrypted = secrets_path.read_bytes()
    try:
        decrypted = _fernet(key).decrypt(encrypted)
    except InvalidToken as exc:
        raise ValueError("Encrypted secrets file could not be decrypted with AUTOOPS_SECRETS_KEY.") from exc

    try:
        payload = json.loads(decrypted.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Encrypted secrets payload is not valid JSON: {exc}") from exc
    return _validate_secret_payload(payload)


def apply_encrypted_secrets(
    path: str | pathlib.Path | None = None,
    key: str | None = None,
    overwrite: bool = False,
) -> dict[str, str]:
    """Load encrypted secrets into os.environ.

    Existing environment variables win by default so Docker/Kubernetes/CI can
    still inject secrets directly.
    """
    loaded = load_encrypted_secrets(path=path, key=key)
    applied: dict[str, str] = {}
    for name, value in loaded.items():
        if overwrite or not os.getenv(name):
            os.environ[name] = value
            applied[name] = value
    return applied


def write_encrypted_secrets(
    secrets: Mapping[str, str],
    path: str | pathlib.Path | None = None,
    key: str | None = None,
) -> pathlib.Path:
    """Validate and write a Fernet-encrypted JSON secrets file."""
    payload = _validate_secret_payload(dict(secrets))
    secrets_path = encrypted_secrets_path(path)
    secrets_path.parent.mkdir(parents=True, exist_ok=True)
    plaintext = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
    secrets_path.write_bytes(_fernet(key).encrypt(plaintext))
    return secrets_path


def encrypted_secrets_status() -> EncryptedSecretsStatus:
    """Return a redacted health summary for /preflight."""
    path = encrypted_secrets_path()
    required = encrypted_secrets_required()
    configured = _configured(path)

    if not configured:
        return EncryptedSecretsStatus(
            configured=False,
            required=required,
            valid=not required,
            loaded_count=0,
            path=str(path),
            detail="not configured",
        )

    try:
        secrets = load_encrypted_secrets(path=path)
    except Exception as exc:
        return EncryptedSecretsStatus(
            configured=True,
            required=required,
            valid=False,
            loaded_count=0,
            path=str(path),
            detail=str(exc),
        )

    return EncryptedSecretsStatus(
        configured=True,
        required=required,
        valid=True,
        loaded_count=len(secrets),
        path=str(path),
        detail=f"valid encrypted secrets file with {len(secrets)} entries",
    )
