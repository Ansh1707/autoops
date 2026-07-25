# AutoOps Active Job Backlog

## Impact

Investigations are queued, planning, running, or reflecting faster than they complete. Users may wait longer for final reports.

## Triage

1. Check `/metrics` and `/slo` for `jobs_active` and status distribution.
2. Inspect worker pods: `kubectl -n autoops get pods -l app.kubernetes.io/component=worker`.
3. Check worker logs for tool errors, model latency, Redis issues, or stuck jobs.
4. Verify Ollama/model endpoint reachability and response time.

## Mitigation

1. Scale workers if CPU/memory and external model capacity allow it.
2. Restart workers if they are wedged: `kubectl -n autoops rollout restart deploy/worker`.
3. Pause non-critical submissions until backlog drains.
4. Investigate and fix repeated tool failures causing retry loops.

## Recovery Validation

1. Active job count falls below the SLO threshold.
2. Jobs transition to `SUCCESS` or actionable `FAILED` states.
3. Worker logs show steady progress rather than repeated failures.
4. New test investigations complete within the expected time.

## Escalation

Escalate when jobs are stuck in one state, Redis queue behavior is abnormal, or the model endpoint is unavailable.

## Prevention

Use bounded tool retries, keep final synthesis deterministic, monitor worker capacity, and add eval coverage for recurring stuck-job patterns.
