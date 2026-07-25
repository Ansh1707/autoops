# AutoOps Failed Jobs

## Impact

One or more investigations ended in `FAILED`. Users may receive no final answer or may need to retry after a fix.

## Triage

1. Fetch the failed job through `/jobs/{job_id}` and inspect `trace`, `current_step`, and `result`.
2. Check worker logs around the failure time.
3. Identify whether the failure came from tool validation, file access, Gmail/OAuth, PDF extraction, model response, Redis, or Postgres.
4. Check audit events for related backup, restore, ingest, or investigation activity.

## Mitigation

1. Fix missing credentials, bad file paths, or unavailable dependencies.
2. Retry the investigation after confirming the root cause.
3. If a code regression caused the failure, roll back the worker image.
4. Add or update deterministic evals when the failure was planner/tool-routing related.

## Recovery Validation

1. The retried job completes with `SUCCESS`.
2. The final report contains a clear answer, not reflection or internal critique text.
3. No new failed jobs appear for the same root cause.
4. Relevant tests or evals pass locally and in CI.

## Escalation

Escalate when failures involve data loss risk, repeated tool execution errors, Gmail account access, restore operations, or suspected security issues.

## Prevention

Keep tool-call validation strict, preserve final-answer contracts, expand eval coverage for new failure modes, and run smoke checks after changing worker behavior.
