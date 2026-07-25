# AutoOps API Down

## Impact

Users cannot submit investigations, check job status, read metrics, or manage backups through the API.

## Triage

1. Check Kubernetes pod state: `kubectl -n autoops get pods -l app.kubernetes.io/component=api`.
2. Check recent API logs: `kubectl -n autoops logs deploy/api --tail=200`.
3. Check readiness locally if a pod is running: `kubectl -n autoops port-forward svc/api 8000:8000` then `curl http://localhost:8000/ready`.
4. Check dependencies: Postgres, Redis, and configured secrets.

## Mitigation

1. Restart the API deployment if pods are wedged: `kubectl -n autoops rollout restart deploy/api`.
2. If readiness fails on database connectivity, inspect Postgres and validate `DATABASE_URL`.
3. If startup fails on auth/bootstrap, verify `autoops-secrets` contains strong production values.
4. If a bad image was deployed, roll back: `kubectl -n autoops rollout undo deploy/api`.

## Recovery Validation

1. `curl http://localhost:8000/health` returns `{"status":"ok"}`.
2. `curl http://localhost:8000/ready` returns `{"status":"ready"}`.
3. `/metrics/prometheus` contains `autoops_process_uptime_seconds`.
4. A test authenticated `/investigate` request queues a job.

## Escalation

Escalate when API pods restart repeatedly, migrations fail, secrets are missing, or both Postgres and Redis are healthy but `/ready` still fails.

## Prevention

Keep the release gate green, deploy immutable image tags, run smoke checks after rollout, and review API startup logs during every production release.
