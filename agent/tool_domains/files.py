import pathlib

from langchain.tools import tool

from agent.tool_domains.shared import (
    INBOX_DIR,
    cap_text,
    iter_text_files,
    resolve_allowed,
    resolve_existing_file,
)


@tool
def read_file(path: str, max_chars: int = 3000) -> str:
    """
    Read a local text file from an allowed project directory.
    Supports .py .md .txt .json .yaml .env and other text formats.
    path can be absolute (/mac/downloads/file.txt) or relative (inbox/file.txt).
    """
    try:
        resolved = resolve_existing_file(path)
        if not resolved.is_file():
            return f"Not a file: {resolved}"
        text = resolved.read_text(encoding="utf-8", errors="ignore")
        return cap_text(text, max_chars)
    except Exception as exc:
        return f"read_file failed: {exc}"


@tool
def list_directory(path: str) -> str:
    """
    List all files and folders inside a directory.
    Use this when the user asks to list files or show what is inside a folder.
    """
    try:
        resolved = resolve_existing_file(path)
        if not resolved.exists():
            return f"Path does not exist: {path}"
        if not resolved.is_dir():
            return f"Not a directory: {path}. Use read_file to read a specific file."

        items = sorted(resolved.iterdir())
        if not items:
            return f"Directory is empty: {path}"

        lines = [f"Contents of {path} ({len(items)} items):\n"]
        for item in items:
            if item.is_dir():
                try:
                    count = sum(1 for _ in item.iterdir())
                except PermissionError:
                    count = "?"
                lines.append(f"  DIR   {item.name}/  ({count} items)")
            else:
                try:
                    size_kb = item.stat().st_size / 1024
                    lines.append(f"  FILE  {item.name}  ({size_kb:.1f} KB)")
                except PermissionError:
                    lines.append(f"  FILE  {item.name}  (size unknown)")

        return "\n".join(lines)
    except Exception as exc:
        return f"list_directory failed: {exc}"


@tool
def search_codebase(directory: str, pattern: str, max_results: int = 25) -> str:
    """
    Search allowed local code and text files for a pattern (case-insensitive).
    Returns file path, line number, and matching line for each hit.
    """
    try:
        root = resolve_allowed(directory)
        if not root.is_dir():
            return f"Not a directory: {root}"

        pattern_lower = pattern.lower()
        matches: list[str] = []
        for file_path in iter_text_files(root):
            rel = file_path.relative_to(root)
            try:
                lines = file_path.read_text(
                    encoding="utf-8", errors="ignore"
                ).splitlines()
                for line_no, line in enumerate(lines, 1):
                    if pattern_lower in line.lower():
                        matches.append(f"{rel}:{line_no}: {line.strip()}")
                        if len(matches) >= max_results:
                            return cap_text("\n".join(matches))
            except Exception:
                continue
        return cap_text("\n".join(matches)) if matches else f"No matches found for '{pattern}'."
    except Exception as exc:
        return f"search_codebase failed: {exc}"


@tool
def scan_inbox() -> str:
    """
    List all files currently in the AutoOps inbox folder (/app/inbox).
    """
    try:
        inbox = pathlib.Path(INBOX_DIR).expanduser().resolve()

        try:
            resolve_allowed(str(inbox))
        except ValueError:
            return (
                f"Inbox path {inbox} is outside allowed roots. "
                f"Check AUTOOPS_INBOX in your docker-compose.yml environment."
            )

        if not inbox.exists():
            inbox.mkdir(parents=True, exist_ok=True)
            return (
                f"Inbox is empty at {inbox}.\n"
                f"Copy any file into ~/Desktop/autoops/inbox/ on your Mac."
            )

        files = [path for path in inbox.iterdir() if path.is_file()]
        if not files:
            return (
                "Inbox is empty.\n"
                "Copy files into ~/Desktop/autoops/inbox/ on your Mac to use them."
            )

        lines = [f"Inbox contents ({len(files)} files):\n"]
        for path in sorted(files):
            size_kb = path.stat().st_size / 1024
            lines.append(f"  {path.name}  ({size_kb:.1f} KB)")

        lines.append(
            f"\nTo use a file ask: 'read inbox/{files[0].name}' "
            f"or 'summarise inbox/{files[0].name}'"
        )
        return "\n".join(lines)
    except Exception as exc:
        return f"scan_inbox failed: {exc}"
