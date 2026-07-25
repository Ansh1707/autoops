from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from agent.planner_utils import (
    classify_goal,
    final_answer_contract,
    needs_final_synthesis,
    recover_tool_call,
)


EVAL_SUITE_VERSION = "2026-07-04.1"
MIN_REQUIRED_CHECKS = 28


@dataclass(frozen=True)
class EvalResult:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class ToolRoutingCase:
    name: str
    goal: str
    model_tool: str
    model_args: dict
    expected_tool: str
    expected_args: dict | None = None
    expected_reason: str | None = None


@dataclass(frozen=True)
class GoalClassificationCase:
    name: str
    goal: str
    expected_type: str
    required_contract_phrases: tuple[str, ...]


@dataclass(frozen=True)
class FinalSynthesisCase:
    name: str
    answer: str
    expected_needs_synthesis: bool


TOOL_ROUTING_CASES = (
    ToolRoutingCase(
        name="pdf_read_is_recovered_to_pdf_summary",
        goal="Summarize /mac/downloads/report.pdf",
        model_tool="read_file",
        model_args={"path": "/mac/downloads/report.pdf"},
        expected_tool="summarise_pdf",
        expected_args={"file_path": "/mac/downloads/report.pdf"},
        expected_reason="read_file_pdf_to_summarise_pdf",
    ),
    ToolRoutingCase(
        name="pdf_search_is_recovered_to_pdf_summary",
        goal="Please summarize /app/inbox/paper.pdf",
        model_tool="search_codebase",
        model_args={"directory": "/app/inbox/paper.pdf", "pattern": "summary"},
        expected_tool="summarise_pdf",
        expected_args={"file_path": "/app/inbox/paper.pdf"},
        expected_reason="search_codebase_pdf_to_summarise_pdf",
    ),
    ToolRoutingCase(
        name="file_listing_is_not_git_history",
        goal="list files in this project",
        model_tool="get_recent_commits",
        model_args={"repo_path": "/app"},
        expected_tool="list_directory",
        expected_args={"path": "/app"},
        expected_reason="git_history_to_list_directory",
    ),
    ToolRoutingCase(
        name="normal_git_question_stays_git",
        goal="show recent commits in this project",
        model_tool="get_recent_commits",
        model_args={"repo_path": "/app"},
        expected_tool="get_recent_commits",
        expected_args={"repo_path": "/app"},
        expected_reason=None,
    ),
)


GOAL_CLASSIFICATION_CASES = (
    GoalClassificationCase(
        name="pdf_summary_contract",
        goal="Summarize /app/inbox/file.pdf",
        expected_type="pdf_summary",
        required_contract_phrases=("Overview", "page citations", "OCR"),
    ),
    GoalClassificationCase(
        name="pdf_question_contract",
        goal="What are the limitations in /app/inbox/file.pdf?",
        expected_type="pdf_question",
        required_contract_phrases=("Evidence", "Do not invent"),
    ),
    GoalClassificationCase(
        name="gmail_contract",
        goal="Check my unread Gmail from today",
        expected_type="gmail",
        required_contract_phrases=("email summary", "Never claim an email was sent"),
    ),
    GoalClassificationCase(
        name="system_contract",
        goal="Check CPU and memory",
        expected_type="system",
        required_contract_phrases=("Summary", "Evidence"),
    ),
)


FINAL_SYNTHESIS_CASES = (
    FinalSynthesisCase(
        name="blocks_reflection_as_final",
        answer="Reflection critique: missing details",
        expected_needs_synthesis=True,
    ),
    FinalSynthesisCase(
        name="blocks_raw_pdf_context_as_final",
        answer="PDF RAG summary context\nFile: paper.pdf\nRetrieved chunks...",
        expected_needs_synthesis=True,
    ),
    FinalSynthesisCase(
        name="allows_real_user_answer",
        answer="Summary\n\nThe PDF explains the project goals and next steps.",
        expected_needs_synthesis=False,
    ),
)


def _check(condition: bool, name: str, detail: str) -> EvalResult:
    return EvalResult(name=name, passed=condition, detail=detail)


def evaluate_tool_routing() -> list[EvalResult]:
    results: list[EvalResult] = []
    for case in TOOL_ROUTING_CASES:
        tool, args, reason = recover_tool_call(case.model_tool, case.model_args, case.goal)
        results.append(
            _check(
                tool == case.expected_tool,
                f"tool_routing::{case.name}::tool",
                f"expected {case.expected_tool}, got {tool}",
            )
        )
        if case.expected_args is not None:
            results.append(
                _check(
                    args == case.expected_args,
                    f"tool_routing::{case.name}::args",
                    f"expected {case.expected_args}, got {args}",
                )
            )
        results.append(
            _check(
                reason == case.expected_reason,
                f"tool_routing::{case.name}::reason",
                f"expected {case.expected_reason}, got {reason}",
            )
        )
    return results


def evaluate_goal_contracts() -> list[EvalResult]:
    results: list[EvalResult] = []
    for case in GOAL_CLASSIFICATION_CASES:
        goal_type = classify_goal(case.goal)
        contract = final_answer_contract(case.goal)
        results.append(
            _check(
                goal_type == case.expected_type,
                f"goal_contract::{case.name}::classification",
                f"expected {case.expected_type}, got {goal_type}",
            )
        )
        for phrase in case.required_contract_phrases:
            results.append(
                _check(
                    phrase.lower() in contract.lower(),
                    f"goal_contract::{case.name}::phrase::{phrase}",
                    f"contract missing phrase: {phrase}",
                )
            )
    return results


def evaluate_final_synthesis_guards() -> list[EvalResult]:
    return [
        _check(
            needs_final_synthesis(case.answer) == case.expected_needs_synthesis,
            f"final_synthesis::{case.name}",
            f"expected {case.expected_needs_synthesis}, got {needs_final_synthesis(case.answer)}",
        )
        for case in FINAL_SYNTHESIS_CASES
    ]


EVAL_GROUPS: tuple[Callable[[], list[EvalResult]], ...] = (
    evaluate_tool_routing,
    evaluate_goal_contracts,
    evaluate_final_synthesis_guards,
)


def run_all_evals() -> list[EvalResult]:
    results: list[EvalResult] = []
    for group in EVAL_GROUPS:
        results.extend(group())
    return results


def summarize_results(results: list[EvalResult]) -> dict:
    passed = sum(1 for result in results if result.passed)
    failed = len(results) - passed
    return {
        "passed": passed,
        "failed": failed,
        "total": len(results),
        "suite": "agent_quality",
        "suite_version": EVAL_SUITE_VERSION,
        "pass_rate": round(passed / len(results), 4) if results else 1.0,
        "failures": [
            {"name": result.name, "detail": result.detail}
            for result in results
            if not result.passed
        ],
    }


def eval_manifest() -> dict:
    return {
        "suite": "agent_quality",
        "suite_version": EVAL_SUITE_VERSION,
        "min_required_checks": MIN_REQUIRED_CHECKS,
        "groups": {
            "tool_routing": len(TOOL_ROUTING_CASES),
            "goal_contracts": len(GOAL_CLASSIFICATION_CASES),
            "final_synthesis_guards": len(FINAL_SYNTHESIS_CASES),
        },
        "coverage": [
            "PDF tool recovery",
            "file listing vs git fallback",
            "goal classification",
            "goal-specific final answer contracts",
            "reflection/raw-context final answer blocking",
        ],
    }
