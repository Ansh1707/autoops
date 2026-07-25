"""Manage AutoOps encrypted local secrets.

Examples:
  python scripts/secrets_tool.py generate-key
  python scripts/secrets_tool.py write JWT_SECRET_KEY=... AUTOOPS_BOOTSTRAP_PASSWORD=...
  python scripts/secrets_tool.py inspect
"""

from __future__ import annotations

import argparse
import sys

from api.secrets import (
    DEFAULT_SECRETS_FILE,
    encrypted_secrets_path,
    generate_secrets_key,
    load_encrypted_secrets,
    write_encrypted_secrets,
)


def _parse_secret_pairs(pairs: list[str]) -> dict[str, str]:
    secrets: dict[str, str] = {}
    for pair in pairs:
        if "=" not in pair:
            raise ValueError(f"Expected KEY=VALUE, got {pair!r}")
        key, value = pair.split("=", 1)
        secrets[key.strip()] = value
    return secrets


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage AutoOps encrypted secrets.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("generate-key", help="Print a new AUTOOPS_SECRETS_KEY value.")

    write_parser = subparsers.add_parser("write", help="Write an encrypted secrets file.")
    write_parser.add_argument("secrets", nargs="+", help="Secrets as KEY=VALUE pairs.")
    write_parser.add_argument("--file", default=DEFAULT_SECRETS_FILE, help="Encrypted secrets output path.")
    write_parser.add_argument("--key", default=None, help="Fernet key. Defaults to AUTOOPS_SECRETS_KEY.")

    inspect_parser = subparsers.add_parser("inspect", help="Validate and list encrypted secret names.")
    inspect_parser.add_argument("--file", default=None, help="Encrypted secrets file path.")
    inspect_parser.add_argument("--key", default=None, help="Fernet key. Defaults to AUTOOPS_SECRETS_KEY.")

    return parser


def _normalize_key_args(argv: list[str] | None) -> list[str] | None:
    if argv is None:
        return None
    normalized: list[str] = []
    index = 0
    while index < len(argv):
        if argv[index] == "--key" and index + 1 < len(argv):
            normalized.append(f"--key={argv[index + 1]}")
            index += 2
            continue
        normalized.append(argv[index])
        index += 1
    return normalized


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(_normalize_key_args(argv))

    try:
        if args.command == "generate-key":
            print(generate_secrets_key())
            return 0

        if args.command == "write":
            path = write_encrypted_secrets(_parse_secret_pairs(args.secrets), path=args.file, key=args.key)
            print(f"Wrote encrypted secrets to {path}")
            return 0

        if args.command == "inspect":
            secrets = load_encrypted_secrets(path=args.file, key=args.key)
            print(f"valid encrypted secrets file with {len(secrets)} entries; path={encrypted_secrets_path(args.file)}")
            for name in sorted(secrets):
                print(f"- {name}")
            return 0

    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
