import json
import pathlib
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evals.agent_quality import eval_manifest, run_all_evals, summarize_results


def main() -> int:
    summary = summarize_results(run_all_evals())
    summary["manifest"] = eval_manifest()
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
