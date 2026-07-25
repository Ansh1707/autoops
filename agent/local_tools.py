"""
Backward-compatible tool imports.

New code should import from agent.tool_domains.* or agent.tool_domains.registry.
This module stays as a stable bridge for API and worker code that still imports
agent.local_tools directly.
"""

from agent.tool_domains.documents import ingest_document
from agent.tool_domains.files import list_directory, read_file, scan_inbox, search_codebase
from agent.tool_domains.git import get_diff, get_recent_commits
from agent.tool_domains.pdf import ask_pdf, inspect_pdf, summarise_pdf
from agent.tool_domains.shell import run_command
from agent.tool_domains.system import get_system_stats


__all__ = [
    "read_file",
    "list_directory",
    "search_codebase",
    "scan_inbox",
    "inspect_pdf",
    "summarise_pdf",
    "ask_pdf",
    "run_command",
    "get_recent_commits",
    "get_diff",
    "ingest_document",
    "get_system_stats",
]
