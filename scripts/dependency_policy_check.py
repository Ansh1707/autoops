"""Dependency reproducibility and SBOM checks for AutoOps."""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from dataclasses import asdict, dataclass
from importlib import metadata


ROOT = pathlib.Path(__file__).resolve().parents[1]
REQUIREMENTS = ROOT / "requirements.txt"
PACKAGE_JSON = ROOT / "frontend" / "package.json"
PACKAGE_LOCK = ROOT / "frontend" / "package-lock.json"
RANGE_PREFIXES = ("^", "~", ">", "<", "*", "x", "X")
REQ_PATTERN = re.compile(r"^([A-Za-z0-9_.-]+)==([A-Za-z0-9_.*+!()-]+)$")


@dataclass(frozen=True)
class PolicyResult:
    check: str
    ok: bool
    detail: str


def _load_json(path: pathlib.Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_requirements(path: pathlib.Path = REQUIREMENTS) -> dict[str, str]:
    requirements: dict[str, str] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = REQ_PATTERN.match(line)
        if not match:
            raise ValueError(f"{path.name}:{line_number} must use exact NAME==VERSION pins: {line}")
        name, version = match.groups()
        requirements[name] = version
    return requirements


def _check_python_requirements() -> list[PolicyResult]:
    try:
        requirements = parse_requirements()
    except Exception as exc:
        return [PolicyResult("python_requirements_exact", False, str(exc))]

    results = [
        PolicyResult(
            "python_requirements_exact",
            True,
            f"{len(requirements)} direct Python dependencies are exactly pinned",
        )
    ]

    mismatches: list[str] = []
    missing: list[str] = []
    for name, expected in requirements.items():
        try:
            installed = metadata.version(name)
        except metadata.PackageNotFoundError:
            missing.append(name)
            continue
        if installed != expected:
            mismatches.append(f"{name}: expected {expected}, installed {installed}")

    results.append(
        PolicyResult(
            "python_environment_matches_requirements",
            not missing and not mismatches,
            "; ".join([*missing, *mismatches]) or "installed Python environment matches requirements.txt",
        )
    )
    return results


def _is_exact_npm_spec(spec: str) -> bool:
    if not spec or spec.startswith(RANGE_PREFIXES):
        return False
    return not any(operator in spec for operator in [">", "<", "||", " - "])


def _frontend_direct_deps(package_json: dict) -> dict[str, str]:
    deps: dict[str, str] = {}
    for section in ("dependencies", "devDependencies"):
        deps.update(package_json.get(section, {}))
    return deps


def _check_frontend_dependencies() -> list[PolicyResult]:
    package_json = _load_json(PACKAGE_JSON)
    package_lock = _load_json(PACKAGE_LOCK)
    direct_deps = _frontend_direct_deps(package_json)
    loose = [f"{name}@{spec}" for name, spec in direct_deps.items() if not _is_exact_npm_spec(spec)]

    results = [
        PolicyResult(
            "frontend_direct_dependencies_exact",
            not loose,
            "; ".join(loose) or f"{len(direct_deps)} frontend direct dependencies are exactly pinned",
        )
    ]

    lock_root = package_lock.get("packages", {}).get("", {})
    lock_direct = {}
    for section in ("dependencies", "devDependencies"):
        lock_direct.update(lock_root.get(section, {}))

    mismatches = [
        f"{name}: package.json={spec}, package-lock={lock_direct.get(name)}"
        for name, spec in direct_deps.items()
        if lock_direct.get(name) != spec
    ]
    results.append(
        PolicyResult(
            "frontend_lock_matches_package_json",
            not mismatches,
            "; ".join(mismatches) or "package-lock root dependency specs match package.json",
        )
    )

    missing_locked_versions = [
        name for name in direct_deps
        if not package_lock.get("packages", {}).get(f"node_modules/{name}", {}).get("version")
    ]
    results.append(
        PolicyResult(
            "frontend_direct_dependencies_locked",
            not missing_locked_versions,
            ", ".join(missing_locked_versions) or "all frontend direct dependencies have locked versions",
        )
    )
    return results


def build_sbom() -> dict:
    python_components = [
        {
            "type": "library",
            "name": name,
            "version": version,
            "purl": f"pkg:pypi/{name}@{version}",
            "scope": "required",
        }
        for name, version in sorted(parse_requirements().items(), key=lambda item: item[0].lower())
    ]

    package_lock = _load_json(PACKAGE_LOCK)
    npm_components = []
    for path, package in sorted(package_lock.get("packages", {}).items()):
        if not path.startswith("node_modules/"):
            continue
        name = path.removeprefix("node_modules/")
        version = package.get("version")
        if version:
            npm_components.append(
                {
                    "type": "library",
                    "name": name,
                    "version": version,
                    "purl": f"pkg:npm/{name}@{version}",
                    "scope": "required" if package.get("dev") is not True else "optional",
                }
            )

    components = python_components + npm_components
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "metadata": {
            "component": {
                "type": "application",
                "name": "autoops",
            }
        },
        "components": components,
        "summary": {
            "python_direct_dependencies": len(python_components),
            "npm_locked_dependencies": len(npm_components),
            "total_components": len(components),
        },
    }


def run_dependency_policy_checks() -> dict:
    results = [
        *_check_python_requirements(),
        *_check_frontend_dependencies(),
    ]
    sbom = build_sbom()
    results.append(
        PolicyResult(
            "sbom_generates",
            sbom["summary"]["total_components"] > 0,
            (
                f"SBOM contains {sbom['summary']['python_direct_dependencies']} Python direct "
                f"and {sbom['summary']['npm_locked_dependencies']} npm locked dependencies"
            ),
        )
    )
    failed = [result for result in results if not result.ok]
    return {
        "ok": not failed,
        "passed": len(results) - len(failed),
        "failed": len(failed),
        "checks": [asdict(result) for result in results],
        "sbom_summary": sbom["summary"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check dependency pinning and generate AutoOps SBOM data.")
    parser.add_argument("--write-sbom", type=pathlib.Path, help="Write the generated SBOM JSON to this path.")
    args = parser.parse_args(argv)

    try:
        summary = run_dependency_policy_checks()
        if args.write_sbom:
            args.write_sbom.parent.mkdir(parents=True, exist_ok=True)
            args.write_sbom.write_text(json.dumps(build_sbom(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0 if summary["ok"] else 1
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
