# AutoOps High API Latency

## Impact

The API remains available but responds slowly. Users may see delayed job submissions, slow dashboard refreshes, or timeout-prone diagnostics.

## Triage

1. Check `/metrics` runtime histograms for high-latency paths.
2. Compare latency with job backlog, database load, and Redis availability.
3. Inspect pod CPU and memory: `kubectl -n autoops top pods`.
4. Check whether slow requests correlate with backups, ingest, PDF processing, or external model calls.

## Mitigation

1. Scale the API if CPU or memory pressure is high: `kubectl -n autoops scale deploy/api --replicas=3`.
2. Pause expensive non-critical workflows such as large ingestion or backup restores.
3. Tune rate limits if a client is flooding the API.
4. Roll back if latency started after a deployment.

## Recovery Validation

1. `/slo` reports `api_request_latency_p95_ms` as healthy.
2. Prometheus latency alert clears for at least 10 minutes.
3. User-facing API routes respond within expected latency.
4. Resource pressure returns to normal.

## Escalation

Escalate if latency remains high after scaling, if Postgres queries are slow, or if worker jobs are starving API resources.

## Prevention

Keep expensive work in the worker path, add indexes or pagination for slow DB paths, and expand load tests for high-traffic routes.
