import subprocess

from langchain.tools import tool

from agent.tool_domains.shared import cap_text, resolve_allowed


@tool
def get_recent_commits(repo_path: str, max_count: int = 10) -> str:
    """
    Get recent git commits from a local repository.
    Use this ONLY for git commit history, not for listing files.
    """
    try:
        repo = resolve_allowed(repo_path)
        result = subprocess.run(
            [
                "git", "-C", str(repo), "log",
                f"--max-count={min(max_count, 25)}",
                "--pretty=format:%h | %cd | %s",
                "--date=short",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if result.returncode != 0:
            return cap_text(result.stderr or "Unable to read git commits. Is this a git repo?")
        return result.stdout or "No commits found."
    except Exception as exc:
        return f"get_recent_commits failed: {exc}"


@tool
def get_diff(repo_path: str, file_path: str = "") -> str:
    """
    Get uncommitted git diff for a repository or specific file.
    Leave file_path empty to see all uncommitted changes.
    """
    try:
        repo = resolve_allowed(repo_path)
        cmd = ["git", "-C", str(repo), "diff"]
        if file_path:
            cmd.extend(["--", file_path])
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=15, check=False
        )
        if result.returncode != 0:
            return cap_text(result.stderr or "Unable to read git diff.")
        return cap_text(result.stdout or "No uncommitted diff.")
    except Exception as exc:
        return f"get_diff failed: {exc}"
