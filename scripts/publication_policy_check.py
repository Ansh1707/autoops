"""Static policy checks for files that must never enter a public release."""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from dataclasses import asdict, dataclass
from typing import Any


ROOT = pathlib.Path(__file__).resolve().parents[1]
GITIGNORE = ROOT / ".gitignore"
DOCKERIGNORE = ROOT / ".dockerignore"
PRIVATE_DIRECTORIES = (
    "data",
    "inbox",
    "notes",
    "papers",
    "uploads",
    "chroma_data",
    "backups",
    "reports",
    "logs",
)
SECRET_RULES = {
    ".env",
    ".env.*",
    "Credentials.json",
    "credentials.json",
    "client_secret*.json",
    "token*.json",
    "gmail_token*.json",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
}


@dataclass(frozen=True)
class PolicyResult:
    check: str
    ok: bool
    detail: str


def _rules(path: pathlib.Path) -> set[str]:
    return {
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def run_publication_policy_checks(
    gitignore: pathlib.Path = GITIGNORE,
    dockerignore: pathlib.Path = DOCKERIGNORE,
    root: pathlib.Path = ROOT,
) -> dict[str, Any]:
    missing_files = [str(path) for path in (gitignore, dockerignore) if not path.exists()]
    if missing_files:
        result = PolicyResult("ignore_files_exist", False, "missing: " + ", ".join(missing_files))
        return {"ok": False, "passed": 0, "failed": 1, "checks": [asdict(result)]}

    git_rules = _rules(gitignore)
    docker_rules = _rules(dockerignore)
    required_git_directory_rules = {
        rule
        for directory in PRIVATE_DIRECTORIES
        for rule in (f"{directory}/*", f"!{directory}/.gitkeep")
    }
    required_docker_directory_rules = {f"{directory}/" for directory in PRIVATE_DIRECTORIES}
    placeholders = [root / directory / ".gitkeep" for directory in PRIVATE_DIRECTORIES]
    valid_placeholders = all(
        path.is_file() and not path.read_text(encoding="utf-8").strip()
        for path in placeholders
    )

    results = [
        PolicyResult(
            "git_private_directory_rules",
            required_git_directory_rules.issubset(git_rules),
            "Git ignores private directory contents while preserving empty placeholders",
        ),
        PolicyResult(
            "docker_private_directory_rules",
            required_docker_directory_rules.issubset(docker_rules),
            "Docker excludes all personal and runtime data directories",
        ),
        PolicyResult(
            "private_directory_placeholders",
            valid_placeholders,
            f"{len(placeholders)} empty private-directory placeholders exist",
        ),
        PolicyResult(
            "git_secret_rules",
            SECRET_RULES.issubset(git_rules),
            "Git excludes environment files, OAuth data, tokens, and private keys",
        ),
        PolicyResult(
            "docker_secret_rules",
            SECRET_RULES.issubset(docker_rules),
            "Docker build contexts exclude environment files, OAuth data, tokens, and private keys",
        ),
        PolicyResult(
            "public_env_template",
            "!.env.example" in git_rules and "!.env.example" in docker_rules,
            ".env.example remains publishable and available to image builds",
        ),
        PolicyResult(
            "os_metadata_excluded",
            ".DS_Store" in git_rules
            and "**/.DS_Store" in git_rules
            and ".DS_Store" in docker_rules
            and "**/.DS_Store" in docker_rules,
            "macOS metadata is excluded from Git and Docker",
        ),
    ]
    failed = [result for result in results if not result.ok]
    return {
        "ok": not failed,
        "passed": len(results) - len(failed),
        "failed": len(failed),
        "checks": [asdict(result) for result in results],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check AutoOps publication safety policy.")
    parser.add_argument("--gitignore", type=pathlib.Path, default=GITIGNORE)
    parser.add_argument("--dockerignore", type=pathlib.Path, default=DOCKERIGNORE)
    parser.add_argument("--root", type=pathlib.Path, default=ROOT)
    args = parser.parse_args(argv)
    try:
        summary = run_publication_policy_checks(args.gitignore, args.dockerignore, args.root)
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0 if summary["ok"] else 1
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
