# AutoOps

AutoOps is a local agentic operations assistant. It accepts an investigation goal, queues the work with Celery, runs a LangGraph/Ollama agent, stores job traces in Postgres, and uses ChromaDB for long-term memory.

## Architecture

- React frontend submits goals and polls job status.
- FastAPI exposes `/token`, `/investigate`, `/jobs/{job_id}`, `/ingest`, `/health`, `/version`, `/ready`, `/metrics`, `/metrics/prometheus`, `/slo`, `/preflight`, `/backups`, and `/audit`.
- Celery workers execute investigations asynchronously through Redis.
- LangGraph coordinates tool use, reflection, and final RCA generation.
- ChromaDB stores incident/document memory.
- Redis stores queue state plus short-term per-job reasoning steps.
- Gmail tools use local OAuth files and default to creating drafts before sending.
- API responses include `X-Request-ID`, and agent/tool events are emitted as structured JSON logs with sensitive fields redacted.
- API startup runs lightweight idempotent schema migrations for local/Docker upgrades and records a migration ledger.
- Local backups export job history, AutoOps-owned document folders, and Chroma state into checksummed zip files.
- Disaster-recovery drills prove backup creation, inspection, dry-run restore, and isolated real restore.
- Sensitive actions write append-only audit events with hash-chain verification.
- API and investigation submission endpoints are protected by local rate limits.
- Database-backed users support role-based access control with `owner`, `admin`, `operator`, and `viewer` roles.
- Optional encrypted local secrets load from `secrets.autoops.enc` before core settings are read.
- Docker images use pinned base tags, deterministic installs, cleaned package caches, and non-root runtime users.
- Python and frontend direct dependencies are exactly pinned, with a release-gated dependency policy and SBOM generator.
- GitHub Actions runs the full release gate with least-privilege permissions, service dependencies, concurrency control, and release-manifest artifacts.
- CI runs pinned Trivy, `pip-audit`, and `npm audit` vulnerability scans for HIGH/CRITICAL findings, and uploads JSON scan reports.
- Trusted pushes build immutable GHCR images with provenance/SBOM attestations and keyless Cosign signatures.
- CI proves the Kubernetes deployment on Kind by applying rendered manifests, waiting for rollouts, and smoke-checking the API service.
- The frontend includes a release-gated operator dashboard for version, metrics, SLOs, jobs, preflight, backups, and audit state.
- Kubernetes manifests enforce restricted pod security, pinned images, probes, resource budgets, PDBs, PVCs, and network policy.
- SLO status and Prometheus alert rules cover API availability, error rate, latency, rate-limit pressure, job backlog, and failed jobs.
- Release provenance records version metadata, source artifact hashes, release-gate checks, and policy summaries.

## Local Features

- Local file reader and code search.
- Safe whitelisted shell command runner with dry-run default.
- Real local git commit and diff inspection.
- Document ingestion into ChromaDB for a personal knowledge base.
- System stats watchdog through `psutil` and a Celery Beat schedule.
- Gmail inbox fetch, full email read, draft creation, and guarded send.
- Fault-tolerant short-term session memory: Redis outages degrade gracefully instead of crashing investigations.

## Safety Defaults

- `AUTOOPS_ALLOWED_ROOTS` controls exactly which local folders file and shell tools can touch.
- `run_command` uses `shell=False`, blocks shell control operators, validates path arguments against allowed roots, and restricts Docker to read-only inspection commands.
- `AUTOOPS_ENV=production` requires a non-default `JWT_SECRET_KEY` and disables the demo mock token unless explicitly enabled.
- `AUTOOPS_CORS_ORIGINS` should list explicit frontend origins; wildcard CORS is rejected in production.
- Gmail sending still requires explicit `confirmation="SEND"` and drafts are preferred by the agent.
- Backups exclude Gmail credentials/tokens by default. Real restore is blocked unless `AUTOOPS_ENABLE_RESTORE=true`; dry-run restore remains available for inspection.
- Audit metadata is sanitized before storage; tokens, passwords, credentials, and secrets are redacted.
- Rate limits are enabled by default: `AUTOOPS_RATE_LIMIT_REQUESTS_PER_MINUTE` for general API traffic and `AUTOOPS_RATE_LIMIT_JOB_SUBMITS_PER_MINUTE` for worker-producing investigation submissions.
- The first local user is bootstrapped from `AUTOOPS_BOOTSTRAP_USERNAME`, `AUTOOPS_BOOTSTRAP_PASSWORD`, and `AUTOOPS_BOOTSTRAP_ROLE`; production refuses the default bootstrap password.
- Encrypted secrets are optional in development and can be made mandatory with `AUTOOPS_REQUIRE_ENCRYPTED_SECRETS=true`.

## Run

```bash
docker compose up --build
```

Frontend: http://localhost:5173
API: http://localhost:8000

Operational endpoints:

```text
GET /health   Basic process liveness
GET /version  Release version, build, environment, and image metadata
GET /ready    Database readiness check
GET /metrics  Job, audit, request, latency, and rate-limit metrics as JSON
GET /metrics/prometheus Prometheus-compatible scrape output
GET /slo      Machine-readable SLO status for dashboards and alert triage
GET /preflight Configuration, path, service, and optional capability diagnostics
GET /backups  List local backup artifacts (auth required)
GET /audit    Recent append-only audit events (auth required)
GET /audit/verify Verify audit hash-chain integrity (auth required)
```

`/preflight` separates required failures from optional capability failures. For example, Redis or database failures make `ok=false`, while missing Gmail credentials or OCR tooling are reported as optional unless you need those features. It also reports `secrets:encrypted_file`, which is required only when `AUTOOPS_REQUIRE_ENCRYPTED_SECRETS=true`. Scanned/image-only PDFs need Tesseract plus Poppler (`pdftoppm`) installed on the runtime that executes the worker.

## API Contract

AutoOps publishes a generated OpenAPI contract at `docs/api/openapi.json` and release-gates the critical API surface:

```bash
python scripts/api_contract_policy_check.py --write
python scripts/api_contract_policy_check.py
```

The contract policy verifies public endpoints stay public, protected endpoints require bearer auth, operation IDs and HTTP 200 responses are documented, core schemas exist, and job/user/audit response models keep the fields used by the dashboard and agent workflows.

## Performance Policy

AutoOps includes a deterministic synthetic performance gate for critical API paths:

```bash
python scripts/performance_policy_check.py
```

The check runs the API against an isolated in-memory database with realistic seeded job volume, exercises public and authenticated endpoints, verifies p95/max latency budgets, confirms high `GET /jobs` limits are bounded, runs concurrent `/jobs` and `/metrics` requests, and proves request counters plus latency histograms are emitted after load.

## Resilience Policy

AutoOps release-gates graceful degradation behavior with synthetic dependency failures:

```bash
python scripts/resilience_policy_check.py
```

The policy proves healthy preflight passes, optional outages such as Gmail credentials, OCR tooling, and Ollama do not block readiness, required Redis/database outages do block readiness with actionable detail, backup encryption-by-default fails without a key, and Redis-backed session memory degrades without raising into the agent loop.

## Encrypted Local Secrets

AutoOps can load a Fernet-encrypted JSON secrets file before reading core settings such as `DATABASE_URL`, `JWT_SECRET_KEY`, `AUTOOPS_BOOTSTRAP_PASSWORD`, `AUTOOPS_BACKUP_ENCRYPTION_KEY`, and Gmail paths.

## Secret Scanning

AutoOps blocks committed credentials at two independent points:

- A pinned Gitleaks `v8.30.1` pre-commit hook scans staged changes locally with redacted output.
- A dedicated GitHub Actions job checks complete Git history, encoded values, and one archive layer before the release gate can run.

Install the local development hook after initializing the Git repository:

```bash
venv/bin/pip install -r requirements-dev.txt
venv/bin/pre-commit install
venv/bin/pre-commit run --all-files
```

The CI scan uses the official versioned Gitleaks container and does not upload raw finding reports. Never bypass the hook for a real credential. Revoke and rotate any credential that has entered Git history, even if the commit has not been pushed.

Generate a secrets key:

```bash
python scripts/secrets_tool.py generate-key
```

Set the key outside Git, then write the encrypted file:

```bash
export AUTOOPS_SECRETS_KEY=<generated-key>
python scripts/secrets_tool.py write \
  JWT_SECRET_KEY=<strong-jwt-secret> \
  AUTOOPS_BOOTSTRAP_PASSWORD=<strong-password> \
  AUTOOPS_BACKUP_ENCRYPTION_KEY=<backup-fernet-key>
```

Validate the file without printing secret values:

```bash
python scripts/secrets_tool.py inspect
```

Production-style runs should set:

```text
AUTOOPS_SECRETS_FILE=secrets.autoops.enc
AUTOOPS_SECRETS_KEY=<generated-key>
AUTOOPS_REQUIRE_ENCRYPTED_SECRETS=true
```

`secrets.autoops.enc`, `.env.*`, Gmail credentials, tokens, personal documents, Chroma data, and backup files are ignored by Git and excluded from Docker build contexts by default. Private runtime directories contain only tracked `.gitkeep` placeholders; every other file placed in `data`, `inbox`, `notes`, `papers`, `uploads`, `chroma_data`, `backups`, `reports`, or `logs` remains local.

## Container Security

The release gate includes a static container hardening check:

```bash
python scripts/container_security_check.py
```

It verifies that AutoOps Dockerfiles use explicit base image tags, avoid root runtime users, clean apt package lists, use `npm ci` for frontend dependency installs, and do not directly copy local secrets or OAuth files into images.

CI also runs pinned vulnerability scans against the checked-out workspace and package lockfiles:

```bash
python scripts/vulnerability_policy_check.py
```

The policy check verifies that CI runs Trivy from an explicit image tag, scans filesystem dependency and misconfiguration data, runs `pip-audit==2.10.1` against `requirements.txt`, runs `npm audit --audit-level=high` against the frontend lockfile, writes JSON evidence under `reports/`, uploads the reports as artifacts, and only then enforces scanner exit codes. Local release checks validate the scanning policy; GitHub Actions performs the live vulnerability scans.

## Dependency Policy And SBOM

AutoOps keeps direct Python and frontend dependencies exactly pinned. The release gate verifies that `requirements.txt`, `frontend/package.json`, and `frontend/package-lock.json` remain deterministic:

```bash
python scripts/dependency_policy_check.py
```

Generate a CycloneDX-style dependency inventory when needed:

```bash
python scripts/dependency_policy_check.py --write-sbom reports/autoops-sbom.json
```

The dependency policy is intentionally offline-friendly. It validates local pins, lockfile consistency, installed Python package versions, and SBOM generation without requiring network access. CI pairs it with live vulnerability database checks from Trivy, `pip-audit`, and `npm audit`.

## Release Provenance

AutoOps exposes runtime build metadata at `/version` and can generate an offline release manifest:

```bash
python scripts/release_manifest.py
python scripts/release_manifest.py --output reports/release-manifest.json
```

The manifest includes the project version, runtime metadata, SHA-256 hashes for release-critical source artifacts, the release-gate check list, policy summaries, and SBOM counts. It intentionally avoids secret values and is included in the release gate so every release has reproducible evidence attached.

## CI Release Gate

GitHub Actions runs the same release-readiness gate used locally:

```bash
python scripts/ci_policy_check.py
python scripts/release_check.py
```

The workflow uses read-only repository permissions, cancels superseded runs, starts Postgres and Redis service containers, installs pinned Python/frontend dependencies, runs the full release gate, runs Trivy, `pip-audit`, and `npm audit` vulnerability scanning, and uploads `reports/release-manifest.json`, `reports/trivy-fs.json`, `reports/pip-audit.json`, and `reports/npm-audit.json` as CI artifacts. The release gate includes static CI, vulnerability, and signing policy checks so workflow hardening cannot silently regress.

## Operator Dashboard

The frontend is an operations dashboard, not only a prompt box:

```bash
python scripts/frontend_dashboard_policy_check.py
```

It surfaces release metadata from `/version`, runtime metrics from `/metrics`, SLO status from `/slo`, live job inventory from `/jobs`, preflight diagnostics, backup state, audit-chain verification, recent audit events, live job traces, and final reports. The release gate checks the dashboard coverage so critical operational surfaces cannot disappear during UI changes.

## Signed Images

Trusted pushes to `main` and release tags run a separate image-release job:

```bash
python scripts/signing_policy_check.py
```

The image-release job waits for the release gate, uses scoped `packages: write` and `id-token: write` permissions, builds API, worker, and frontend images for GHCR, enables BuildKit provenance and SBOM attestations, signs immutable image digests with keyless Cosign, verifies the expected GitHub OIDC identity, and uploads `reports/image-signatures.json` plus `reports/cosign-verify-*.json` as signing evidence.

## Kubernetes Deployment Proof

CI also proves that AutoOps can deploy to a real Kubernetes API using Kind:

```bash
python scripts/deployment_policy_check.py
python scripts/render_k8s_deployment_proof.py --output reports/kind/autoops-kind.yaml
```

The deployment-proof job builds local API, worker, and frontend images, loads them into Kind, renders the production Kubernetes template with local immutable images and smaller CI replicas, creates the required Secret dynamically outside Git, applies the manifest, waits for Redis, Postgres, API, worker, beat, and frontend rollouts, port-forwards the API Service, checks `/health` and `/version`, and uploads cluster evidence from `reports/kind/`.

## Kubernetes Production Template

The Kubernetes manifest is a production-oriented template:

```bash
python scripts/k8s_policy_check.py
```

It verifies pinned images, no committed Kubernetes Secret resources, non-root pods, disabled service-account token mounting, `RuntimeDefault` seccomp, dropped Linux capabilities, probes, resource requests/limits, read-only app root filesystems, non-root Chroma cache paths, required NetworkPolicies, and disruption budgets.

Create the required Kubernetes Secret outside Git before applying the manifest:

```bash
kubectl create namespace autoops
kubectl -n autoops create secret generic autoops-secrets \
  --from-literal=postgres-password='<strong-postgres-password>' \
  --from-literal=database-url='postgresql://postgres:<strong-postgres-password>@postgres:5432/autoops' \
  --from-literal=jwt-secret-key='<strong-jwt-secret>' \
  --from-literal=bootstrap-password='<strong-bootstrap-password>' \
  --from-literal=backup-encryption-key='<fernet-backup-key>'
kubectl apply -f k8s/autoops-cluster.yaml
```

Before a real deployment, replace the placeholder image registry `ghcr.io/your-org/...:2026.07.05` with your published immutable image tags or digests.

## SLOs And Alerts

AutoOps exposes live SLO status:

```bash
curl http://localhost:8000/slo
```

The default objectives are:

```text
AUTOOPS_SLO_MAX_ACTIVE_JOBS=20
AUTOOPS_SLO_MAX_FAILED_JOB_RATIO=0.25
AUTOOPS_SLO_MAX_P95_LATENCY_MS=2000
```

Prometheus alert rules live in `k8s/prometheus-alerts.yaml`. The release gate verifies alert coverage, severity labels, `for` durations, expressions, and runbook annotations:

```bash
python scripts/alert_policy_check.py
```

The alert set covers API availability, 5xx rate, API latency, rate-limit pressure, active job backlog, and failed-job growth.
Each alert links to a local runbook in `docs/runbooks/`; the alert policy check verifies that every runbook exists and includes impact, triage, mitigation, recovery validation, escalation, and prevention sections.

## Rate Limits

AutoOps protects the local API from runaway scripts, tight polling loops, and accidental repeated job submissions. The API returns HTTP `429` with `Retry-After`, `X-RateLimit-Limit`, `X-RateLimit-Remaining`, and `X-RateLimit-Window` headers when a limit is hit.

```text
AUTOOPS_RATE_LIMIT_ENABLED=true
AUTOOPS_RATE_LIMIT_REQUESTS_PER_MINUTE=600
AUTOOPS_RATE_LIMIT_JOB_SUBMITS_PER_MINUTE=20
AUTOOPS_RATE_LIMIT_WINDOW_SECONDS=60
```

## Users And RBAC

AutoOps uses database-backed users with PBKDF2 password hashing. The default local bootstrap user is `admin` / `password` with the `owner` role for development. Set a real bootstrap password before production use:

```text
AUTOOPS_BOOTSTRAP_USERNAME=admin
AUTOOPS_BOOTSTRAP_PASSWORD=<strong-password>
AUTOOPS_BOOTSTRAP_ROLE=owner
```

Roles are hierarchical:

```text
viewer   read job/audit/backup metadata
operator submit investigations, ingest documents, create backups
admin    restore backups and manage users
owner    full access
```

## Database Upgrades

AutoOps creates the current schema on first run and applies small idempotent upgrades on startup through `api.migrations.ensure_schema`. Existing local databases that predate fields like `current_step`, `trace`, `result`, or `updated_at` are upgraded automatically when the API starts.

Versioned migration evidence lives in `db/migrations` and is release-gated:

```bash
python scripts/migration_policy_check.py
```

The migration policy verifies ordered migration IDs, manifest checksums, forward-only headers, idempotent DDL, required tables and columns, the `schema_migrations` ledger table, audit hash-chain fields, and the RBAC username index. Destructive DDL such as `DROP TABLE`, `DROP COLUMN`, `TRUNCATE`, and unreviewed `ALTER TABLE` fails the release gate.

## Backups

The Docker setup mounts `./backups` into the API, worker, and beat containers. Creating a backup writes a checksummed zip containing `manifest.json`, `database/investigation_jobs.json`, and AutoOps-owned folders such as `inbox`, `papers`, `notes`, `data`, and Chroma state.

To enable encrypted backups, generate a Fernet key and set it in your environment:

```bash
python scripts/generate_backup_key.py
```

Then set:

```text
AUTOOPS_BACKUP_ENCRYPTION_KEY=<generated-key>
AUTOOPS_BACKUP_ENCRYPT_BY_DEFAULT=true
```

```bash
curl -H "Authorization: Bearer $AUTOOPS_TOKEN" \
  -X POST http://localhost:8000/backups \
  -H "Content-Type: application/json" \
  -d '{"include_files": true, "encrypt": true}'
```

Restore is dry-run by default:

```bash
curl -H "Authorization: Bearer $AUTOOPS_TOKEN" \
  -X POST http://localhost:8000/backups/<backup-id>/restore \
  -H "Content-Type: application/json" \
  -d '{"dry_run": true}'
```

## Disaster Recovery Drill

Run an isolated backup/restore drill without touching your live database:

```bash
python scripts/dr_drill.py
```

The drill creates a temporary source database, writes a backup, validates the manifest and checksum, runs a dry-run restore, then enables a real restore only against an isolated temporary target database.

Exercise encrypted backups too:

```bash
python scripts/dr_drill.py --encrypt
```

Operational target:

```text
RPO target: one valid backup after important local work
RTO target: dry-run restore validated in under 5 minutes for normal local datasets
Restore safety: real restores require AUTOOPS_ENABLE_RESTORE=true and should first pass dry-run restore
```

## Audit Trail

AutoOps records sensitive local actions such as investigation submission, document ingestion, backup creation/inspection/listing, and backup restore attempts. Audit rows include actor, action, resource, request id, sanitized metadata, previous hash, and event hash.

```bash
curl -H "Authorization: Bearer $AUTOOPS_TOKEN" http://localhost:8000/audit
curl -H "Authorization: Bearer $AUTOOPS_TOKEN" http://localhost:8000/audit/verify
```

For local Ollama, make sure the configured model is pulled:

```bash
ollama pull qwen2.5:3b
```

## Gmail Setup

Place your OAuth desktop credentials at `credentials.json` or `Credentials.json` in the project root. On first Gmail tool use, a browser consent flow creates `token.json`.

These files are intentionally ignored by git:

```text
credentials.json
Credentials.json
token.json
gmail_token.json
```

## Useful Goals

```text
Summarize unread Gmail from the last 24 hours.
Search this repo for every place JWT auth is used.
Run pytest in dry-run mode and tell me what command would execute.
Show recent git commits for this project.
Ingest README.md into my personal knowledge base.
Check local system stats and warn me if anything looks critical.
```

## Tests

```bash
pytest tests/ -v
```

## Release Gate

Run the same release-readiness gate used by CI:

```bash
python scripts/release_check.py
```

It validates Python compilation, backend tests, deterministic agent evals, release manifest generation, Docker Compose config, frontend lint, and frontend build.
It also runs container hardening, dependency policy, CI workflow policy, vulnerability scanning policy, image-signing policy, Kubernetes deployment-proof policy, frontend dashboard policy, API contract policy, performance policy, migration policy, resilience policy, Kubernetes manifest policy, alert policy, and disaster-recovery drill checks so infrastructure regressions fail before deployment.

## Smoke Check

After Docker is running, verify the API lifecycle, auth, preflight diagnostics, and job submission path:

```bash
python scripts/smoke_check.py
```

## Agent Evals

Run deterministic regression evals for tool routing, final-answer guards, and goal-specific answer contracts:

```bash
python scripts/run_evals.py
```

The eval runner is CI-friendly: it prints a JSON summary and exits non-zero on any failed case.

## Cloud Deployment Path

For AWS, map Postgres to RDS, Redis to ElastiCache, the API/worker/frontend to EKS or ECS, Chroma persistence to EBS/EFS, and secrets to AWS Secrets Manager or Kubernetes Secrets.
