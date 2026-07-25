import os
import pathlib
import re
from typing import Iterable


MAX_TOOL_CHARS = int(os.getenv("MAX_TOOL_OUTPUT_CHARS", "4000"))
INBOX_DIR = os.getenv("AUTOOPS_INBOX", os.path.join(os.getcwd(), "inbox"))


def configured_allowed_roots() -> str:
    return os.getenv("AUTOOPS_ALLOWED_ROOTS", os.getcwd())


def cap_text(text: str, limit: int = MAX_TOOL_CHARS) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n...[truncated to {limit} chars]"


def allowed_roots() -> list[pathlib.Path]:
    roots = []
    for raw in configured_allowed_roots().split(os.pathsep):
        raw = raw.strip()
        if raw:
            roots.append(pathlib.Path(raw).expanduser().resolve())
    return roots or [pathlib.Path.cwd().resolve()]


def resolve_allowed(path: str) -> pathlib.Path:
    candidate = pathlib.Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = pathlib.Path.cwd() / candidate
    resolved = candidate.resolve()

    for root in allowed_roots():
        try:
            resolved.relative_to(root)
            return resolved
        except ValueError:
            continue

    allowed = "\n  ".join(str(root) for root in allowed_roots())
    raise ValueError(
        f"Path is outside allowed roots.\nAllowed roots:\n  {allowed}\n"
        f"To access this file, copy it into {INBOX_DIR} first."
    )


def resolve_existing_file(path: str) -> pathlib.Path:
    """Resolve a file path and recover from common LLM filename rewrites."""
    resolved = resolve_allowed(path)
    if resolved.exists():
        return resolved

    parent = resolved.parent
    if not parent.exists() or not parent.is_dir():
        return resolved

    requested = resolved.name
    candidates = []
    for item in parent.iterdir():
        if not item.is_file():
            continue
        name = item.name
        if name == requested:
            return item
        if name.lower() == requested.lower():
            candidates.append(item)
            continue
        if requested.endswith(name):
            candidates.append(item)
            continue
        stripped_requested = re.sub(r"^\d{4}[-_\s]+", "", requested)
        stripped_name = re.sub(r"^\d{4}[-_\s]+", "", name)
        if stripped_requested.lower() == stripped_name.lower():
            candidates.append(item)

    if len(candidates) == 1:
        return candidates[0]
    return resolved


def iter_text_files(root: pathlib.Path) -> Iterable[pathlib.Path]:
    ignored_dirs = {
        ".git", ".pytest_cache", "__pycache__",
        "node_modules", "venv", ".venv", "chroma_data",
    }
    suffixes = {
        ".py", ".ts", ".tsx", ".js", ".jsx",
        ".md", ".txt", ".json", ".yaml", ".yml",
        ".toml", ".env",
    }
    for path in root.rglob("*"):
        if any(part in ignored_dirs for part in path.parts):
            continue
        if path.is_file() and path.suffix.lower() in suffixes:
            yield path
