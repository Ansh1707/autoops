# AutoOps Rate Limit Pressure

## Impact

Clients are being blocked by AutoOps rate limits. This protects the system, but users or automations may see HTTP 429 responses.

## Triage

1. Check `/metrics` for `autoops_api_rate_limit_blocks_total` labels by path.
2. Review API logs for repeated clients, paths, and request IDs.
3. Determine whether traffic is legitimate user activity, aggressive polling, a script loop, or abuse.
4. Check frontend polling intervals and external automation schedules.

## Mitigation

1. Stop or slow the offending client if it is accidental automation.
2. Increase rate-limit thresholds only when the traffic is legitimate and capacity is healthy.
3. Add client-side backoff when repeated 429s come from known clients.
4. If abuse is suspected, block the source at ingress or network policy.

## Recovery Validation

1. 429 rate drops below the alert threshold.
2. Legitimate workflows succeed without repeated 429s.
3. API CPU and latency remain stable.
4. `/slo` remains healthy.

## Escalation

Escalate when rate-limit pressure looks malicious, originates from unknown networks, or causes important user workflows to fail.

## Prevention

Keep `Retry-After` handling in clients, use bounded polling, and review automation schedules before adding new recurring jobs.
