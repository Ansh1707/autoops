"""OpenAPI contract checks for the AutoOps API surface."""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from dataclasses import asdict, dataclass
from typing import Any


ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OPENAPI_FILE = ROOT / "docs" / "api" / "openapi.json"
REQUIRED_PUBLIC_OPERATIONS = {
    "/health": ("get",),
    "/ready": ("get",),
    "/version": ("get",),
    "/metrics": ("get",),
    "/metrics/prometheus": ("get",),
    "/slo": ("get",),
    "/preflight": ("get",),
    "/token": ("post",),
}
REQUIRED_PROTECTED_OPERATIONS = {
    "/audit": ("get",),
    "/audit/verify": ("get",),
    "/backups": ("get", "post"),
    "/backups/{backup_id}": ("get",),
    "/backups/{backup_id}/restore": ("post",),
    "/users/me": ("get",),
    "/users": ("get", "post"),
    "/investigate": ("post",),
    "/jobs": ("get",),
    "/jobs/{job_id}": ("get",),
    "/ingest": ("post",),
}
REQUIRED_SCHEMAS = (
    "GoalRequest",
    "JobResponse",
    "LoginRequest",
    "UserResponse",
    "AuditEventResponse",
    "BackupCreateRequest",
    "RestoreRequest",
    "IngestRequest",
)
REQUIRED_JOB_FIELDS = (
    "id",
    "goal",
    "status",
    "current_step",
    "trace",
    "result",
    "created_at",
    "updated_at",
)


@dataclass(frozen=True)
class PolicyResult:
    check: str
    ok: bool
    detail: str


def load_openapi() -> dict[str, Any]:
    from api.main import app

    return app.openapi()


def write_openapi_contract(output: pathlib.Path = OPENAPI_FILE) -> pathlib.Path:
    spec = load_openapi()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(spec, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return output


def _operation(spec: dict[str, Any], path: str, method: str) -> dict[str, Any] | None:
    candidate = spec.get("paths", {}).get(path, {}).get(method)
    return candidate if isinstance(candidate, dict) else None


def _has_bearer_security(operation: dict[str, Any] | None) -> bool:
    security = operation.get("security") if operation else None
    if not isinstance(security, list):
        return False
    return any(isinstance(entry, dict) and "HTTPBearer" in entry for entry in security)


def _response_schema_ref(operation: dict[str, Any] | None, status_code: str = "200") -> str:
    if not operation:
        return ""
    schema = (
        operation.get("responses", {})
        .get(status_code, {})
        .get("content", {})
        .get("application/json", {})
        .get("schema", {})
    )
    if "$ref" in schema:
        return str(schema["$ref"])
    if schema.get("type") == "array":
        return str(schema.get("items", {}).get("$ref", ""))
    return ""


def run_api_contract_policy_checks(
    openapi_file: pathlib.Path = OPENAPI_FILE,
    *,
    require_snapshot: bool = True,
) -> dict[str, Any]:
    spec = load_openapi()
    paths = spec.get("paths", {})
    schemas = spec.get("components", {}).get("schemas", {})
    security_schemes = spec.get("components", {}).get("securitySchemes", {})
    results: list[PolicyResult] = [
        PolicyResult("openapi_title", spec.get("info", {}).get("title") == "AutoOps API", "AutoOps API"),
        PolicyResult("openapi_version", bool(spec.get("info", {}).get("version")), "info.version is populated"),
        PolicyResult("bearer_security_scheme", "HTTPBearer" in security_schemes, "HTTP bearer auth is declared"),
    ]

    if require_snapshot:
        results.append(PolicyResult("snapshot_exists", openapi_file.exists(), str(openapi_file.relative_to(ROOT))))
        if openapi_file.exists():
            snapshot = json.loads(openapi_file.read_text(encoding="utf-8"))
            results.append(
                PolicyResult(
                    "snapshot_matches_generated_contract",
                    snapshot == spec,
                    "docs/api/openapi.json matches app.openapi()",
                )
            )

    for path, methods in {**REQUIRED_PUBLIC_OPERATIONS, **REQUIRED_PROTECTED_OPERATIONS}.items():
        results.append(PolicyResult(f"path_{path}", path in paths, path))
        for method in methods:
            operation = _operation(spec, path, method)
            results.append(PolicyResult(f"operation_{method}_{path}", operation is not None, f"{method.upper()} {path}"))
            results.append(
                PolicyResult(
                    f"operation_id_{method}_{path}",
                    bool(operation and operation.get("operationId")),
                    f"{method.upper()} {path} has operationId",
                )
            )
            results.append(
                PolicyResult(
                    f"response_200_{method}_{path}",
                    bool(operation and "200" in operation.get("responses", {})),
                    f"{method.upper()} {path} documents HTTP 200",
                )
            )

    for path, methods in REQUIRED_PUBLIC_OPERATIONS.items():
        for method in methods:
            results.append(
                PolicyResult(
                    f"public_no_auth_{method}_{path}",
                    not _has_bearer_security(_operation(spec, path, method)),
                    f"{method.upper()} {path} remains public",
                )
            )

    for path, methods in REQUIRED_PROTECTED_OPERATIONS.items():
        for method in methods:
            results.append(
                PolicyResult(
                    f"protected_bearer_{method}_{path}",
                    _has_bearer_security(_operation(spec, path, method)),
                    f"{method.upper()} {path} requires bearer auth",
                )
            )

    for schema_name in REQUIRED_SCHEMAS:
        results.append(PolicyResult(f"schema_{schema_name}", schema_name in schemas, schema_name))

    job_properties = schemas.get("JobResponse", {}).get("properties", {})
    for field in REQUIRED_JOB_FIELDS:
        results.append(PolicyResult(f"job_response_field_{field}", field in job_properties, field))

    results.extend([
        PolicyResult(
            "jobs_list_schema",
            _response_schema_ref(_operation(spec, "/jobs", "get")).endswith("/JobResponse"),
            "GET /jobs returns JobResponse[]",
        ),
        PolicyResult(
            "job_detail_schema",
            _response_schema_ref(_operation(spec, "/jobs/{job_id}", "get")).endswith("/JobResponse"),
            "GET /jobs/{job_id} returns JobResponse",
        ),
        PolicyResult(
            "users_schema",
            _response_schema_ref(_operation(spec, "/users", "get")).endswith("/UserResponse"),
            "GET /users returns UserResponse[]",
        ),
        PolicyResult(
            "audit_schema",
            _response_schema_ref(_operation(spec, "/audit", "get")).endswith("/AuditEventResponse"),
            "GET /audit returns AuditEventResponse[]",
        ),
    ])

    failed = [result for result in results if not result.ok]
    return {
        "ok": not failed,
        "passed": len(results) - len(failed),
        "failed": len(failed),
        "checks": [asdict(result) for result in results],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check the AutoOps OpenAPI contract.")
    parser.add_argument("--write", action="store_true", help="Regenerate docs/api/openapi.json before checking.")
    parser.add_argument("--output", type=pathlib.Path, default=OPENAPI_FILE)
    parser.add_argument("--no-snapshot", action="store_true", help="Do not require docs/api/openapi.json.")
    args = parser.parse_args(argv)

    if args.write:
        write_openapi_contract(args.output)
    summary = run_api_contract_policy_checks(args.output, require_snapshot=not args.no_snapshot)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
