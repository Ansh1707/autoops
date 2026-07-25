from agent.memory import search_past_incidents
from agent.tool_domains.devops import get_metrics, search_logs
from agent.tool_domains.documents import ingest_document
from agent.tool_domains.files import list_directory, read_file, scan_inbox, search_codebase
from agent.tool_domains.git import get_diff, get_recent_commits
from agent.tool_domains.gmail import create_gmail_draft, fetch_recent_emails, read_full_email, send_email
from agent.tool_domains.pdf import ask_pdf, inspect_pdf, summarise_pdf
from agent.tool_domains.shell import run_command
from agent.tool_domains.system import get_system_stats


TOOLS = [
    search_logs,
    get_metrics,
    search_past_incidents,
    read_file,
    list_directory,
    search_codebase,
    scan_inbox,
    inspect_pdf,
    summarise_pdf,
    ask_pdf,
    run_command,
    get_recent_commits,
    get_diff,
    ingest_document,
    get_system_stats,
    fetch_recent_emails,
    read_full_email,
    create_gmail_draft,
    send_email,
]

TOOL_MAP = {tool.name: tool for tool in TOOLS}
