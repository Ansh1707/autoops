from typing import Any


def normalize_tool_call(raw_call: Any, index: int) -> tuple[str, dict, str]:
    """Return safe tool name, args, and id from a model-generated tool call."""
    if not isinstance(raw_call, dict):
        return "unknown", {}, f"invalid_tool_call_{index}"

    name = str(raw_call.get("name") or "").strip()
    args = raw_call.get("args") or {}
    call_id = str(raw_call.get("id") or f"tool_call_{index}")

    if not isinstance(args, dict):
        args = {"input": args}

    return name, args, call_id


def needs_final_synthesis(answer: str) -> bool:
    cleaned = answer.strip()
    if not cleaned:
        return True

    lowered = cleaned.lower()
    bad_prefixes = (
        "reflection critique",
        "tool ",
        "pdf direct summary context",
        "pdf rag summary context",
        "pdf rag answer context",
    )
    if lowered.startswith(bad_prefixes):
        return True

    bad_phrases = (
        "please re-plan",
        "re-plan and gather",
        "did not provide",
        "i need to use",
    )
    return any(phrase in lowered for phrase in bad_phrases)


def classify_goal(goal: str) -> str:
    lowered = goal.lower()
    if ".pdf" in lowered and any(word in lowered for word in ("summarize", "summarise", "summary")):
        return "pdf_summary"
    if ".pdf" in lowered and any(word in lowered for word in ("ask", "what", "why", "how", "compare", "find")):
        return "pdf_question"
    if any(word in lowered for word in ("email", "gmail", "inbox")):
        return "gmail"
    if any(word in lowered for word in ("git", "commit", "diff")):
        return "git"
    if any(word in lowered for word in ("cpu", "memory", "disk", "system")):
        return "system"
    return "general"


def final_answer_contract(goal: str) -> str:
    goal_type = classify_goal(goal)
    if goal_type == "pdf_summary":
        return (
            "Return Markdown with these sections when supported by the PDF context: "
            "Overview, Main Points, Important Details/Evidence, Conclusions/Recommendations, "
            "Limitations or Missing Information, Action Items/Next Steps, Key Terms/Names/Dates. "
            "Use page citations like [p. 2] for important claims when page numbers are available. "
            "Adapt sections to the PDF type. Only use job-description sections if the PDF is clearly a JD. "
            "If extraction is empty or sparse, say OCR is needed."
        )
    if goal_type == "pdf_question":
        return (
            "Answer the user's PDF question directly in Markdown. Cite page numbers when available. "
            "Include a short Evidence section and say when the PDF context does not specify an answer. "
            "Do not invent facts outside the retrieved PDF context."
        )
    if goal_type == "gmail":
        return (
            "Return a concise email summary grouped by sender/topic. Include urgent items, action items, "
            "and drafts created if any. Never claim an email was sent unless the send tool confirmed it."
        )
    return (
        "Return a concise Markdown answer with Summary, Evidence, and Next Steps when relevant. "
        "Do not include internal planning, raw tool context, or reflection critique."
    )


def recover_tool_call(tool_name: str, tool_args: dict, goal: str) -> tuple[str, dict, str | None]:
    """Apply one deterministic fallback when the model picks a common wrong tool."""
    lowered_goal = goal.lower()

    if tool_name == "read_file":
        path = str(tool_args.get("path") or tool_args.get("file_path") or "")
        if path.lower().endswith(".pdf"):
            return "summarise_pdf", {"file_path": path}, "read_file_pdf_to_summarise_pdf"

    if tool_name == "search_codebase" and ".pdf" in lowered_goal:
        path = str(tool_args.get("directory") or tool_args.get("path") or tool_args.get("file_path") or "")
        if path.lower().endswith(".pdf"):
            return "summarise_pdf", {"file_path": path}, "search_codebase_pdf_to_summarise_pdf"

    if tool_name == "get_recent_commits" and any(word in lowered_goal for word in ("list files", "show files", "what is in")):
        path = str(tool_args.get("repo_path") or tool_args.get("path") or "/app")
        return "list_directory", {"path": path}, "git_history_to_list_directory"

    return tool_name, tool_args, None
