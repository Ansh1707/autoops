import operator
import os
import time
from datetime import datetime, timezone
from typing import Annotated, Callable, Optional, Sequence, TypedDict

from dotenv import load_dotenv
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_ollama import ChatOllama
from langgraph.graph import END, StateGraph

from agent.observability import build_event, log_payload
from agent.planner_utils import (
    final_answer_contract,
    needs_final_synthesis,
    normalize_tool_call,
    recover_tool_call,
)
from agent.session_memory import load_steps, save_step
from agent.tool_domains.registry import TOOL_MAP, TOOLS

if os.getenv("AUTOOPS_LOAD_DOTENV", "true").lower() == "true":
    load_dotenv()

# ── LLM ───────────────────────────────────────────────────────────────────────

ollama_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
ollama_model = os.getenv("OLLAMA_MODEL", "qwen2.5:3b")


def make_llm(bind_tool_list: bool = False):
    model = ChatOllama(
        model=ollama_model,
        temperature=0,
        base_url=ollama_url,
    )
    return model.bind_tools(TOOLS) if bind_tool_list else model

llm = make_llm(bind_tool_list=True)


# ── State ──────────────────────────────────────────────────────────────────────

class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], operator.add]
    reflection_count: int
    job_id: Optional[str]
    step_counter: int
    step_callback: Optional[Callable]
    original_goal: str


# ── Graph nodes ───────────────────────────────────────────────────────────────

def agent_node(state: AgentState) -> dict:
    response = llm.invoke(state["messages"])
    return {"messages": [response]}


def tool_node(state: AgentState) -> dict:
    last_message = state["messages"][-1]
    tool_responses: list[ToolMessage] = []
    step_counter: int = state.get("step_counter", 0)
    callback: Optional[Callable] = state.get("step_callback")

    for index, tool_call in enumerate(getattr(last_message, "tool_calls", []) or []):
        original_tool_name, original_tool_args, tool_call_id = normalize_tool_call(tool_call, index)
        tool_name, tool_args, fallback_reason = recover_tool_call(
            original_tool_name,
            original_tool_args,
            state.get("original_goal", ""),
        )
        tool_fn = TOOL_MAP.get(tool_name)
        started_at = time.monotonic()

        if not tool_name:
            result = "Error: model produced a tool call with no tool name."
        elif tool_fn is None:
            result = f"Error: unknown tool '{tool_name}'"
        else:
            try:
                result = tool_fn.invoke(tool_args)
            except Exception as exc:
                result = f"Tool '{tool_name}' raised an error: {exc}"

        duration_ms = int((time.monotonic() - started_at) * 1000)

        step_counter += 1

        tool_event = build_event(
            "tool_call",
            job_id=state.get("job_id"),
            step=step_counter,
            original_tool=original_tool_name,
            tool=tool_name,
            args=tool_args,
            fallback_reason=fallback_reason,
            duration_ms=duration_ms,
            result=str(result)[:2000],
        )
        save_step(state.get("job_id"), tool_event)
        log_payload(tool_event)

        if callback is not None:
            try:
                callback(step_counter, tool_name)
            except Exception:
                pass

        tool_responses.append(
            ToolMessage(content=str(result), tool_call_id=tool_call_id)
        )

    return {"messages": tool_responses, "step_counter": step_counter}


def reflection_node(state: AgentState) -> dict:
    reflection_llm = make_llm()

    history_text = "\n".join(
        f"[{m.type}] {str(m.content)[:300]}"
        for m in state["messages"][-10:]
    )

    prompt = (
        "You are a senior engineer reviewing an AI agent's investigation.\n"
        f"Investigation history (last 10 messages):\n{history_text}\n\n"
        "Did the agent fully answer the original goal?\n"
        "If yes, reply with exactly: COMPLETE\n"
        "If no, reply with one sentence describing what is still missing."
    )

    critique = reflection_llm.invoke([HumanMessage(content=prompt)])
    new_count = state.get("reflection_count", 0) + 1

    if "COMPLETE" in critique.content.upper():
        return {"reflection_count": new_count}

    return {
        "messages": [
            HumanMessage(
                content=f"Reflection critique: {critique.content}. "
                        "Please re-plan and gather what is missing."
            )
        ],
        "reflection_count": new_count,
    }


# ── Edge conditions ───────────────────────────────────────────────────────────

def should_continue(state: AgentState) -> str:
    last_message = state["messages"][-1]
    if getattr(last_message, "tool_calls", None):
        return "continue"
    return "reflect"


def should_end(state: AgentState) -> str:
    if state.get("reflection_count", 0) >= 2:
        return "end"
    last = state["messages"][-1]
    content = str(last.content).upper()
    if "COMPLETE" in content or "REFLECTION CRITIQUE" not in content:
        return "end"
    return "agent"


def synthesize_final_answer(goal: str, messages: Sequence[BaseMessage]) -> str:
    """Produce a user-facing final answer when the graph ends on critique or raw context."""
    history_text = "\n\n".join(
        f"[{m.type.upper()}]\n{str(m.content)[:2500]}"
        for m in messages[-12:]
    )
    prompt = f"""
Original user goal:
{goal}

Conversation/tool context:
{history_text}

Write the final answer for the user now.

Final answer contract:
{final_answer_contract(goal)}

Rules:
- Do not output reflection critique text as the final answer.
- Do not output raw tool context as the final answer.
- Answer the user's original request directly.
- For any PDF summary, choose sections that match the document type.
- For general PDFs, include: overview, main points, important details/evidence, conclusions/recommendations, limitations or missing information, and action items if any.
- Only if the PDF is clearly a job-description PDF, include:
  1. Role overview
  2. Responsibilities
  3. Required skills
  4. Preferred skills
  5. Qualifications/eligibility
  6. Tools/technologies mentioned
  7. How to prepare for this role
  8. Short fit checklist
- Do not force job-description sections on non-JD PDFs.
- If an expected section is not present in the PDF context, write "Not specified in the PDF".
- Be concise but complete.
"""
    response = make_llm().invoke([HumanMessage(content=prompt)])
    return str(response.content)


# ── Graph assembly ────────────────────────────────────────────────────────────

workflow = StateGraph(AgentState)
workflow.add_node("agent", agent_node)
workflow.add_node("action", tool_node)
workflow.add_node("reflect", reflection_node)

workflow.set_entry_point("agent")
workflow.add_conditional_edges(
    "agent", should_continue, {"continue": "action", "reflect": "reflect"}
)
workflow.add_edge("action", "agent")
workflow.add_conditional_edges(
    "reflect", should_end, {"end": END, "agent": "agent"}
)

app = workflow.compile()


# ── Public entry point ────────────────────────────────────────────────────────

def run_investigation(
    goal: str,
    job_id: Optional[str] = None,
    step_callback: Optional[Callable[[int, str], None]] = None,
) -> tuple[str, list[dict]]:

    previous_steps = load_steps(job_id)
    memory_context = ""
    if previous_steps:
        recent = previous_steps[-10:]
        memory_context = f"\n\nPrior steps for this job:\n{recent}"

    today = datetime.now().strftime("%Y-%m-%d")

    system_prompt = f"""You are AutoOps, a local agentic DevOps and personal assistant running inside Docker on a Mac M1.
Current date: {today}.

═══════════════════════════════════════════════════════
AVAILABLE FILE PATHS — these are real mounted directories
═══════════════════════════════════════════════════════
/mac/downloads   → Mac Downloads folder  (~/Downloads)
/mac/documents   → Mac Documents folder  (~/Documents)
/mac/desktop     → Mac Desktop folder    (~/Desktop)
/app             → AutoOps project folder
/app/inbox       → inbox drop-zone for files to process
/app/papers      → research papers and PDFs
/app/notes       → personal notes
/app/data        → datasets and CSV files

These paths ARE accessible. Do NOT say you cannot access them.
Do NOT ask for clarification about paths. Call the correct tool directly.

═══════════════════════════════════════════════════════
TOOL SELECTION — follow these rules exactly
═══════════════════════════════════════════════════════
"list files in X" / "what is in X folder" / "show X folder"
  → call list_directory with path=X

"what is in my inbox" / "show inbox"
  → call scan_inbox (no arguments needed)

"read file X" / "show contents of X" / "open X"
  → call read_file with path=X
  → NEVER use read_file on .pdf files — use summarise_pdf instead

"summarise X.pdf" / "summarise the PDF X" / "what does X.pdf say"
  → call summarise_pdf with file_path=X
  → preserve the exact filename the user gave; do NOT add today's date or any prefix
  → summarise_pdf uses PDF RAG: page-aware extraction, Chroma indexing, retrieval
  → after the tool returns, write the summary immediately using the returned PDF context
  → choose summary sections based on the PDF type
  → for general PDFs, include overview, main points, evidence/details, conclusion, limitations, and next steps
  → only for job descriptions, include role overview, responsibilities, skills, qualifications, preparation, and fit checklist
  → cite pages like [p. 2] when page evidence is available
  → read_file on a PDF returns binary garbage — NEVER do that

"ask X.pdf about Y" / "what are the limitations/methodology/results in X.pdf"
  → call ask_pdf with file_path=X and question=Y
  → preserve the exact filename the user gave; do NOT add today's date or any prefix
  → ask_pdf retrieves relevant PDF chunks before answering
  → answer with page citations when available

"inspect X.pdf" / "is this PDF readable" / "does this PDF need OCR"
  → call inspect_pdf with file_path=X

"search for X in my files/code"
  → call search_codebase

"recent commits" / "what changed in git"
  → call get_recent_commits
  → NEVER use get_recent_commits to list files

"check my system" / "CPU" / "memory" / "disk"
  → call get_system_stats

"run command X"
  → call run_command with dry_run=True first

"read my emails" / "check emails" / "unread emails"
  → call fetch_recent_emails

═══════════════════════════════════════════════════════
PATH TRANSLATION — always apply these automatically
═══════════════════════════════════════════════════════
"my Downloads" / "~/Downloads" / "Downloads folder"  → /mac/downloads
"my Documents" / "~/Documents" / "Documents folder"  → /mac/documents
"my Desktop"   / "~/Desktop"   / "Desktop folder"    → /mac/desktop
"my inbox"     / "the inbox"                          → /app/inbox
"this project" / "the project" / "autoops"            → /app

═══════════════════════════════════════════════════════
STRICT RULES
═══════════════════════════════════════════════════════
- NEVER say "I don't have access to file systems" — you do have access.
- NEVER say "I cannot access paths outside my environment" — you can.
- NEVER ask the user to clarify a path that matches the mappings above.
- ALWAYS call a tool instead of explaining why you cannot.
- NEVER rename files. NEVER prepend dates like {today} or 2026- to filenames.
- If user gives "AI_Engineer_Intern_JD.pdf", call the tool with exactly that filename.
- NEVER call read_file on any .pdf file — always use summarise_pdf.
- For targeted PDF questions, use ask_pdf rather than dumping the whole PDF.
- For PDF summaries and answers, cite pages when the tool provides page numbers.
- If PDF extraction is poor or empty, say OCR is needed rather than inventing content.
- For Gmail: prefer operators like is:unread newer_than:1d
- For email sending: always use create_gmail_draft first
- For shell commands: always use dry_run=True first
- When user says "today": use {today}
"""

    inputs: AgentState = {
        "messages": [
            SystemMessage(content=system_prompt),
            HumanMessage(content=f"{goal}{memory_context}"),
        ],
        "reflection_count": 0,
        "job_id": job_id,
        "step_counter": 0,
        "step_callback": step_callback,
        "original_goal": goal,
    }

    config = {"recursion_limit": 15}
    final_state = app.invoke(inputs, config=config)

    trace_log: list[dict] = []
    for msg in final_state["messages"]:
        serialized_tool_calls = []
        for index, tool_call in enumerate(getattr(msg, "tool_calls", []) or []):
            tool_name, tool_args, tool_call_id = normalize_tool_call(tool_call, index)
            serialized_tool_calls.append({
                "id": tool_call_id,
                "name": tool_name,
                "args": tool_args,
            })

        trace_log.append({
            "type": msg.type,
            "content": str(msg.content)[:3000],
            "tool_calls": serialized_tool_calls,
        })

    final_answer = str(final_state["messages"][-1].content)
    if needs_final_synthesis(final_answer):
        final_answer = synthesize_final_answer(goal, final_state["messages"])
    final_event = build_event(
        "final_answer",
        job_id=job_id,
        goal=goal,
        contract=final_answer_contract(goal),
        result=final_answer[:3000],
    )
    save_step(job_id, final_event)
    log_payload(final_event)
    return final_answer, trace_log
