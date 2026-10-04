from evals.live_model import CASES, assess, public_report


def test_live_case_requires_tool_and_grounded_answer():
    case = CASES[0]
    trace = [
        {"type": "ai", "tool_calls": [
            {"name": "read_file", "args": {"path": "service-notes.txt"}}]},
        {"type": "tool", "content": "Service: Atlas\nPort: 8123"},
    ]
    result = assess(case, "Atlas runs on port 8123.", trace)
    assert result["passed"]
    assert result["selected_tools"] == ["read_file"]
    assert result["executed_tools"] == ["read_file"]


def test_live_case_catches_unsupported_answer_and_tool_error():
    case = CASES[0]
    trace = [
        {"type": "ai", "tool_calls": [
            {"name": "read_file", "args": {"path": "missing.txt"}}]},
        {"type": "tool", "content": "read_file failed: missing file"},
    ]
    result = assess(case, "Atlas runs on port 8123.", trace)
    assert not result["passed"]
    assert "tool returned an error" in result["failures"]


def test_live_case_accepts_markdown_emphasis_in_grounded_phrase():
    case = CASES[1]
    trace = [
        {"type": "ai", "tool_calls": [
            {"name": "search_codebase", "args": {"directory": "fixtures",
                                                 "pattern": "ROLLBACK_MARKER"}}]},
        {"type": "tool", "content": "ROLLBACK_MARKER: restore last known good image"},
    ]
    result = assess(case, "**restore the last known good image**", trace)
    assert result["passed"]


def test_live_case_rejects_answer_unsupported_by_tool_result():
    case = CASES[0]
    trace = [
        {"type": "ai", "tool_calls": [
            {"name": "read_file", "args": {"path": "service-notes.txt"}}]},
        {"type": "tool", "content": "Service: Atlas"},
    ]
    result = assess(case, "Atlas runs on port 8123.", trace)
    assert "tool result missing '8123'" in result["failures"]


def test_public_report_removes_local_project_path():
    report = {"request": CASES[0].goal}
    assert "/Users/" not in public_report(report)["request"]
