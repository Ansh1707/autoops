from __future__ import annotations

import os
import pathlib
from datetime import UTC, datetime


ROOT = pathlib.Path(__file__).resolve().parents[1]
VERSION_FILE = ROOT / "VERSION"
DEFAULT_VERSION = "0.0.0-dev"
BUILD_ENV_KEYS = {
    "build_sha": "AUTOOPS_BUILD_SHA",
    "build_ref": "AUTOOPS_BUILD_REF",
    "build_time": "AUTOOPS_BUILD_TIME",
    "image_tag": "AUTOOPS_IMAGE_TAG",
}


def read_version(version_file: pathlib.Path = VERSION_FILE) -> str:
    env_version = os.getenv("AUTOOPS_VERSION", "").strip()
    if env_version:
        return env_version
    try:
        version = version_file.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return DEFAULT_VERSION
    return version or DEFAULT_VERSION


def build_metadata() -> dict[str, str]:
    metadata = {
        "name": "autoops",
        "version": read_version(),
        "environment": os.getenv("AUTOOPS_ENV", "development"),
        "reported_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }
    for output_key, env_key in BUILD_ENV_KEYS.items():
        metadata[output_key] = os.getenv(env_key, "unknown")
    return metadata
