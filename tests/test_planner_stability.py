from agent.planner_utils import (
    classify_goal,
    final_answer_contract,
    needs_final_synthesis,
    normalize_tool_call,
    recover_tool_call,
)


def test_normalize_tool_call_handles_valid_call():
    name, args, call_id = normalize_tool_call(
        {"name": "read_file", "args": {"path": "/app/README.md"}, "id": "abc"},
        0,
    )

    assert name == "read_file"
    assert args == {"path": "/app/README.md"}
    assert call_id == "abc"


def test_normalize_tool_call_handles_bad_shapes():
    name, args, call_id = normalize_tool_call("not-a-dict", 2)

    assert name == "unknown"
    assert args == {}
    assert call_id == "invalid_tool_call_2"


def test_normalize_tool_call_wraps_non_dict_args():
    name, args, call_id = normalize_tool_call(
        {"name": "run_command", "args": "pytest tests/"},
        1,
    )

    assert name == "run_command"
    assert args == {"input": "pytest tests/"}
    assert call_id == "tool_call_1"


def test_needs_final_synthesis_rejects_internal_text():
    assert needs_final_synthesis("Reflection critique: missing details")
    assert needs_final_synthesis("PDF direct summary context\nFile: test.pdf")
    assert needs_final_synthesis("Please re-plan and gather what is missing.")


def test_needs_final_synthesis_accepts_real_answer():
    assert not needs_final_synthesis("Here is the summary of the PDF:\n\n1. Overview...")


def test_recover_tool_call_routes_pdf_read_to_summarise_pdf():
    tool, args, reason = recover_tool_call(
        "read_file",
        {"path": "/mac/downloads/report.pdf"},
        "Summarize /mac/downloads/report.pdf",
    )

    assert tool == "summarise_pdf"
    assert args == {"file_path": "/mac/downloads/report.pdf"}
    assert reason == "read_file_pdf_to_summarise_pdf"


def test_recover_tool_call_routes_file_listing_away_from_git():
    tool, args, reason = recover_tool_call(
        "get_recent_commits",
        {"repo_path": "/app"},
        "list files in this project",
    )

    assert tool == "list_directory"
    assert args == {"path": "/app"}
    assert reason == "git_history_to_list_directory"


def test_final_answer_contract_is_goal_specific():
    assert classify_goal("Summarize /app/file.pdf") == "pdf_summary"
    assert "Overview" in final_answer_contract("Summarize /app/file.pdf")
    assert "email summary" in final_answer_contract("Check my Gmail").lower()
