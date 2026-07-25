# AutoOps High API Error Rate

## Impact

Users can reach the API, but a meaningful share of requests return 5xx errors. Investigations, backup operations, auth, or diagnostics may be unreliable.

## Triage

1. Inspect `/metrics` and `/metrics/prometheus` for affected paths and status codes.
2. Check API logs for exceptions grouped by `path`, `request_id`, and `status_code`.
3. Check dependency health: `/ready`, `/preflight`, Redis, Postgres, Chroma storage, and mounted file paths.
4. Compare error start time with the last deployment or configuration change.

## Mitigation

1. Roll back a suspect deployment: `kubectl -n autoops rollout undo deploy/api`.
2. If errors are from a dependency outage, restore that dependency before restarting the API.
3. If errors are from a bad request pattern, use rate limits or temporarily disable the offending client.
4. If the error is isolated to one route, avoid that route while preserving read-only diagnostics.

## Recovery Validation

1. 5xx rate falls below the alert threshold for at least 10 minutes.
2. `/ready`, `/preflight`, `/metrics`, and `/slo` are healthy.
3. Recent API logs no longer show repeated stack traces.
4. A representative user workflow succeeds end to end.

## Escalation

Escalate when 5xx errors persist after rollback, database migrations are involved, or data integrity could be affected.

## Prevention

Add regression tests for the failing route, ensure smoke checks cover the workflow, and keep `/preflight` required checks strict for production.
