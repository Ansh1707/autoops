# AutoOps Runbooks

These runbooks map one-to-one to `k8s/prometheus-alerts.yaml`.

Each alert runbook must include:

- `## Impact`
- `## Triage`
- `## Mitigation`
- `## Recovery Validation`
- `## Escalation`
- `## Prevention`

The release gate runs `python scripts/alert_policy_check.py`, which verifies every required alert has an actionable runbook.

## Alert Runbooks

- [AutoOps API Down](autoops-api-down.md)
- [AutoOps High API Error Rate](autoops-high-api-error-rate.md)
- [AutoOps High API Latency](autoops-high-api-latency.md)
- [AutoOps Rate Limit Pressure](autoops-rate-limit-pressure.md)
- [AutoOps Active Job Backlog](autoops-active-job-backlog.md)
- [AutoOps Failed Jobs](autoops-failed-jobs.md)
