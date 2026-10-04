"""Small, opt-in end-to-end evaluation of the running Ollama agent."""

from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from agent.planner_utils import needs_final_synthesis, recover_tool_call


ROOT = pathlib.Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "evals" / "fixtures"


@dataclass(frozen=True)
class LiveCase:
    name: str
    goal: str
    expected_tool: str
    answer_markers: tuple[str, ...]


CASES = (
    LiveCase(
        "read_service_note",
        f"Read {FIXTURES / 'service-notes.txt'} and tell me the service name and port.",
        "read_file",
        ("Atlas", "8123"),
    ),
    LiveCase(
        "search_rollback_note",
        f"Search {FIXTURES} for ROLLBACK_MARKER and report the procedure.",
        "search_codebase",
        ("restore", "last known good image"),
    ),
    LiveCase(
        "list_fixture_files",
        f"List files in {FIXTURES}.",
        "list_directory",
        ("service-notes.txt",),
    ),
)


def assess(case: LiveCase, answer: str, trace: list[dict]) -> dict:
    selected: list[str] = []
    executed: list[str] = []
    observations: list[str] = []
    for message in trace:
        if message.get("type") == "ai":
            for call in message.get("tool_calls", []):
                name = call.get("name", "")
                selected.append(name)
                executed.append(recover_tool_call(name, call.get("args", {}), case.goal)[0])
        elif message.get("type") == "tool":
            observations.append(str(message.get("content", "")))

    failures = []
    if case.expected_tool not in executed:
        failures.append(f"expected tool {case.expected_tool} was not executed")
    if len(observations) < len(executed):
        failures.append("missing tool observation")
    if any(
        observation.startswith(("Error:", "Tool '", "read_file failed:",
                                "search_codebase failed:", "list_directory failed:"))
        for observation in observations
    ):
        failures.append("tool returned an error")
    if needs_final_synthesis(answer) or not answer.strip():
        failures.append("final answer is empty or internal agent text")
    normalized_answer = " ".join(re.sub(r"[*_`]", "", answer).lower().split())
    normalized_evidence = " ".join(" ".join(observations).lower().split())
    for marker in case.answer_markers:
        if " ".join(marker.lower().split()) not in normalized_answer:
            failures.append(f"final answer missing {marker!r}")
        if " ".join(marker.lower().split()) not in normalized_evidence:
            failures.append(f"tool result missing {marker!r}")
    return {
        "selected_tools": selected,
        "executed_tools": executed,
        "final_answer": answer,
        "failures": failures,
        "passed": not failures,
    }


def _run_child(case: LiveCase) -> None:
    from agent import planner

    allowed = {"read_file", "search_codebase", "list_directory"}
    planner.TOOL_MAP = {name: tool for name, tool in planner.TOOL_MAP.items()
                        if name in allowed}
    answer, trace = planner.run_investigation(case.goal)
    print(json.dumps({"answer": answer, "trace": trace}))


def run_suite(model: str, base_url: str, timeout: int) -> dict:
    if timeout < 1:
        raise ValueError("timeout must be positive")
    results = []
    with tempfile.TemporaryDirectory(prefix="autoops-live-evals-") as chroma_dir:
        for case in CASES:
            env = os.environ.copy()
            env.update({
                "AUTOOPS_LOAD_DOTENV": "false",
                "AUTOOPS_ALLOWED_ROOTS": str(FIXTURES),
                "CHROMA_PERSIST_DIR": chroma_dir,
                "OLLAMA_MODEL": model,
                "OLLAMA_BASE_URL": base_url,
            })
            started = time.monotonic()
            try:
                completed = subprocess.run(
                    [sys.executable, str(ROOT / "scripts" / "run_live_evals.py"),
                     "--case", case.name],
                    cwd=ROOT,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    check=False,
                )
                if completed.returncode:
                    result = {"selected_tools": [], "executed_tools": [],
                              "final_answer": "", "passed": False,
                              "failures": [f"agent process exited {completed.returncode}"]}
                else:
                    raw = json.loads(completed.stdout)
                    result = assess(case, raw["answer"], raw["trace"])
            except subprocess.TimeoutExpired:
                result = {"selected_tools": [], "executed_tools": [],
                          "final_answer": "", "passed": False,
                          "failures": [f"timeout after {timeout}s"]}
            except (ValueError, KeyError) as exc:
                result = {"selected_tools": [], "executed_tools": [],
                          "final_answer": "", "passed": False,
                          "failures": [f"invalid agent output: {type(exc).__name__}"]}
            result.update({"name": case.name, "request": case.goal,
                           "expected_tool": case.expected_tool,
                           "duration_seconds": round(time.monotonic() - started, 2)})
            results.append(result)
    passed = sum(result["passed"] for result in results)
    return {
        "suite": "live_model_tasks",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "model": model,
        "base_url": base_url,
        "timeout_seconds": timeout,
        "passed": passed,
        "failed": len(results) - passed,
        "total": len(results),
        "cases": results,
    }


def public_report(report: dict) -> dict:
    """Replace machine-specific paths before sharing a run."""
    return json.loads(json.dumps(report).replace(str(ROOT), "<repo>"))
