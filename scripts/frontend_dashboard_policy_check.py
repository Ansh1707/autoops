"""Static policy checks for the AutoOps operator dashboard."""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from dataclasses import asdict, dataclass


ROOT = pathlib.Path(__file__).resolve().parents[1]
APP_FILE = ROOT / "frontend" / "src" / "App.tsx"
CSS_FILE = ROOT / "frontend" / "src" / "index.css"
REQUIRED_ENDPOINTS = (
    "/version",
    "/metrics",
    "/slo",
    "/jobs?limit=12",
    "/preflight",
    "/backups",
    "/audit?limit=6",
    "/audit/verify",
)
REQUIRED_UI_TEXT = (
    "Operations Dashboard",
    "Release",
    "SLO Health",
    "Recent Jobs",
    "Job Status",
    "Audit Trail",
    "Backups",
    "Runtime Preflight",
)
REQUIRED_CSS = (
    ".dashboard-band",
    ".dashboard-grid",
    ".ops-grid",
    ".metric-card",
    ".job-row",
    ".slo-row",
)


@dataclass(frozen=True)
class PolicyResult:
    check: str
    ok: bool
    detail: str


def _display_path(path: pathlib.Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def run_frontend_dashboard_policy_checks(
    app_file: pathlib.Path = APP_FILE,
    css_file: pathlib.Path = CSS_FILE,
) -> dict:
    app_text = app_file.read_text(encoding="utf-8") if app_file.exists() else ""
    css_text = css_file.read_text(encoding="utf-8") if css_file.exists() else ""
    results = [
        PolicyResult("app_file_exists", app_file.exists(), _display_path(app_file) if app_file.exists() else "missing"),
        PolicyResult("css_file_exists", css_file.exists(), _display_path(css_file) if css_file.exists() else "missing"),
    ]

    for endpoint in REQUIRED_ENDPOINTS:
        results.append(PolicyResult(f"endpoint_{endpoint}", endpoint in app_text, endpoint))

    for label in REQUIRED_UI_TEXT:
        results.append(PolicyResult(f"ui_{label.lower().replace(' ', '_')}", label in app_text, label))

    for selector in REQUIRED_CSS:
        results.append(PolicyResult(f"css_{selector.removeprefix('.')}", selector in css_text, selector))

    results.extend([
        PolicyResult("auto_refresh", "setInterval(refreshOps, 15000)" in app_text, "dashboard refreshes automatically"),
        PolicyResult("live_job_inventory", "setLiveJobs" in app_text and "recentJobs" in app_text, "dashboard uses API job inventory"),
        PolicyResult("responsive_dashboard", "@media (max-width: 860px)" in css_text and ".dashboard-grid" in css_text, "dashboard has responsive layout rules"),
    ])
    failed = [result for result in results if not result.ok]
    return {
        "ok": not failed,
        "passed": len(results) - len(failed),
        "failed": len(failed),
        "checks": [asdict(result) for result in results],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check AutoOps frontend dashboard coverage.")
    parser.add_argument("--app", type=pathlib.Path, default=APP_FILE)
    parser.add_argument("--css", type=pathlib.Path, default=CSS_FILE)
    args = parser.parse_args(argv)
    try:
        summary = run_frontend_dashboard_policy_checks(args.app, args.css)
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0 if summary["ok"] else 1
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
