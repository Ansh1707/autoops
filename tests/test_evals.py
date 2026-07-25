from evals.agent_quality import MIN_REQUIRED_CHECKS, eval_manifest, run_all_evals, summarize_results


def test_agent_quality_evals_pass():
    summary = summarize_results(run_all_evals())

    assert summary["failed"] == 0, summary
    assert summary["pass_rate"] == 1.0
    assert summary["total"] >= MIN_REQUIRED_CHECKS


def test_eval_manifest_tracks_required_coverage():
    manifest = eval_manifest()

    assert manifest["suite"] == "agent_quality"
    assert manifest["min_required_checks"] == MIN_REQUIRED_CHECKS
    assert manifest["groups"]["tool_routing"] >= 4
    assert "PDF tool recovery" in manifest["coverage"]
