import pytest


pytestmark = pytest.mark.usefixtures("tmp_path")


def _load_registry(tmp_path, monkeypatch):
    monkeypatch.setenv("CHROMA_PERSIST_DIR", str(tmp_path / "chroma"))
    from agent.tool_domains.registry import TOOL_MAP, TOOLS

    return TOOL_MAP, TOOLS


try:
    import langchain  # noqa: F401
except Exception as exc:
    pytest.skip(f"Tool registry requires runtime dependencies: {exc}", allow_module_level=True)


def test_tool_registry_has_unique_tool_names(tmp_path, monkeypatch):
    _, TOOLS = _load_registry(tmp_path, monkeypatch)
    names = [tool.name for tool in TOOLS]
    assert len(names) == len(set(names))


def test_tool_registry_contains_all_core_domains(tmp_path, monkeypatch):
    TOOL_MAP, _ = _load_registry(tmp_path, monkeypatch)
    expected_tools = {
        "search_logs",
        "get_metrics",
        "search_past_incidents",
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
        "fetch_recent_emails",
        "read_full_email",
        "create_gmail_draft",
        "send_email",
    }
    assert expected_tools.issubset(TOOL_MAP.keys())


def test_tool_registry_uses_domain_modules(tmp_path, monkeypatch):
    TOOL_MAP, _ = _load_registry(tmp_path, monkeypatch)

    assert TOOL_MAP["search_logs"].func.__module__ == "agent.tool_domains.devops"
    assert TOOL_MAP["get_metrics"].func.__module__ == "agent.tool_domains.devops"
    assert TOOL_MAP["fetch_recent_emails"].func.__module__ == "agent.gmail_tools"
