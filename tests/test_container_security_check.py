from scripts import container_security_check


def test_container_security_checks_current_dockerfiles():
    summary = container_security_check.run_container_security_checks()

    assert summary["ok"] is True
    assert summary["failed"] == 0
    assert {check["file"] for check in summary["checks"]} == {
        "Dockerfile.api",
        "Dockerfile.worker",
        "frontend/Dockerfile.frontend",
        "docker-compose.yml",
    }


def test_container_policy_rejects_broad_base_image_and_root_user(tmp_path):
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(
        "\n".join(
            [
                "FROM python:3.11-slim",
                "WORKDIR /app",
                "RUN apt-get update && apt-get install -y gcc",
                "USER root",
            ]
        ),
        encoding="utf-8",
    )

    results = container_security_check.check_dockerfile(dockerfile)
    failures = {result.check: result.detail for result in results if not result.ok}

    assert "base_image_pinned" in failures
    assert "non_root_user" in failures
    assert "apt_hygiene" in failures


def test_container_policy_rejects_direct_secret_copy(tmp_path):
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(
        "\n".join(
            [
                "FROM python:3.11.9-slim-bookworm",
                "COPY credentials.json /app/credentials.json",
                "USER autoops",
            ]
        ),
        encoding="utf-8",
    )

    results = container_security_check.check_dockerfile(dockerfile)
    secret_check = next(result for result in results if result.check == "no_secret_copy")

    assert secret_check.ok is False


def test_compose_policy_rejects_root_cache_path(tmp_path):
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(
        "\n".join(
            [
                "services:",
                "  api:",
                "    environment:",
                "      - CHROMA_PERSIST_DIR=/root/.cache/chroma_data",
                "    volumes:",
                "      - chroma_cache:/root/.cache",
            ]
        ),
        encoding="utf-8",
    )

    results = container_security_check.check_compose(compose)
    failures = {result.check for result in results if not result.ok}

    assert "non_root_cache_path" in failures
    assert "chroma_persist_dir_configured" in failures


def test_compose_policy_rejects_literal_secrets_and_personal_mounts(tmp_path):
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(
        "\n".join(
            [
                "services:",
                "  postgres:",
                "    environment:",
                "      - POSTGRES_PASSWORD=postgres",
                "  api:",
                "    environment:",
                "      - DATABASE_URL=postgresql://postgres:postgres@postgres:5432/autoops",
                "      - JWT_SECRET_KEY=dev-secret",
                "      - AUTOOPS_BOOTSTRAP_PASSWORD=password",
                "      - CHROMA_PERSIST_DIR=/app/.cache/chroma_data",
                "    volumes:",
                "      - /Users/example/Downloads:/mac/downloads:ro",
            ]
        ),
        encoding="utf-8",
    )

    results = container_security_check.check_compose(compose)
    failures = {result.check for result in results if not result.ok}

    assert "no_hardcoded_database_credentials" in failures
    assert "runtime_secrets_required" in failures
    assert "portable_host_mounts" in failures
    assert "postgres_data_persistent" in failures
