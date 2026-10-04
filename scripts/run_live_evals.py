"""Run actual agent tasks against a local Ollama model (not part of CI)."""

from __future__ import annotations

import argparse
import json
import pathlib
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evals.live_model import CASES, _run_child, public_report, run_suite


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="qwen2.5:3b")
    parser.add_argument("--base-url", default="http://localhost:11434")
    parser.add_argument("--timeout", type=int, default=180,
                        help="maximum seconds per task")
    parser.add_argument("--output", type=pathlib.Path,
                        help="optional JSON report path")
    parser.add_argument("--case", choices=[case.name for case in CASES],
                        help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.case:
        _run_child(next(case for case in CASES if case.name == args.case))
        return 0
    report = public_report(run_suite(args.model, args.base_url, args.timeout))
    serialized = json.dumps(report, indent=2, ensure_ascii=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding="utf-8")
    print(serialized, end="")
    return 1 if report["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
