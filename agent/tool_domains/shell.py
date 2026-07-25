import shlex
import subprocess

from langchain.tools import tool

from agent.tool_domains.shared import allowed_roots, cap_text, resolve_allowed


ALLOWED_COMMANDS = {
    "docker",
    "df",
    "du",
    "git",
    "ls",
    "pip",
    "pytest",
    "python",
    "python3",
    "pwd",
    "cat",
    "wc",
    "head",
    "tail",
    "find",
    "grep",
}

DOCKER_READ_ONLY_SUBCOMMANDS = {
    "compose",
    "container",
    "images",
    "info",
    "inspect",
    "logs",
    "ps",
    "stats",
    "version",
}
DOCKER_COMPOSE_READ_ONLY_SUBCOMMANDS = {"config", "logs", "ps", "top"}
SHELL_CONTROL_TOKENS = {";", "&&", "||", "|", ">", ">>", "<", "$(", "`"}
PATH_FLAGS_WITH_VALUE = {
    "-C",
    "--directory",
    "--file",
    "--path",
}


def _looks_like_path(arg: str) -> bool:
    return (
        arg.startswith("/")
        or arg.startswith("~")
        or arg.startswith(".")
        or "/.." in arg
        or "../" in arg
    )


def _validate_path_arg(arg: str) -> str | None:
    try:
        resolve_allowed(arg)
        return None
    except ValueError as exc:
        return str(exc)


def validate_command(parts: list[str]) -> str | None:
    if not parts:
        return "No command provided."
    if parts[0] not in ALLOWED_COMMANDS:
        return f"Command '{parts[0]}' is not allowed.\nAllowed: {sorted(ALLOWED_COMMANDS)}"
    if any(token in arg for arg in parts for token in SHELL_CONTROL_TOKENS):
        return "Shell control operators are not allowed. Run one simple command at a time."

    if parts[0] == "docker":
        if len(parts) < 2:
            return "Docker command must include a read-only subcommand."
        subcommand = parts[1]
        if subcommand not in DOCKER_READ_ONLY_SUBCOMMANDS:
            return f"Docker subcommand '{subcommand}' is not allowed. Allowed: {sorted(DOCKER_READ_ONLY_SUBCOMMANDS)}"
        if subcommand == "compose" and len(parts) >= 3 and parts[2] not in DOCKER_COMPOSE_READ_ONLY_SUBCOMMANDS:
            return (
                f"Docker compose subcommand '{parts[2]}' is not allowed. "
                f"Allowed: {sorted(DOCKER_COMPOSE_READ_ONLY_SUBCOMMANDS)}"
            )

    skip_next_path_validation = False
    for index, arg in enumerate(parts[1:], 1):
        if skip_next_path_validation:
            error = _validate_path_arg(arg)
            if error:
                return error
            skip_next_path_validation = False
            continue
        if arg in PATH_FLAGS_WITH_VALUE:
            skip_next_path_validation = True
            continue
        if _looks_like_path(arg):
            error = _validate_path_arg(arg)
            if error:
                return error

    return None


@tool
def run_command(command: str, dry_run: bool = True, timeout_seconds: int = 20) -> str:
    """
    Run a whitelisted shell command on the local machine.
    dry_run=True by default; pass dry_run=False only when execution is intended.
    """
    try:
        parts = shlex.split(command)
        validation_error = validate_command(parts)
        if validation_error:
            return validation_error
        if dry_run:
            return f"[DRY RUN] Would execute: {command}\nPass dry_run=False to actually run this."

        result = subprocess.run(
            parts,
            capture_output=True,
            text=True,
            timeout=min(timeout_seconds, 60),
            cwd=str(allowed_roots()[0]),
            check=False,
        )
        output = result.stdout + result.stderr
        return cap_text(f"exit_code={result.returncode}\n{output}")
    except subprocess.TimeoutExpired:
        return f"Command timed out after {timeout_seconds} seconds."
    except Exception as exc:
        return f"run_command failed: {exc}"
