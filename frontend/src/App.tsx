import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import './index.css';

interface TokenResponse {
  access_token: string;
  token_type: string;
  role: string;
}

interface InvestigateResponse {
  job_id: string;
  status: string;
}

interface TraceStep {
  type: string;
  content: string;
  tool_calls?: { id?: string; name: string; args: Record<string, unknown> }[];
}

interface JobResponse {
  id: string;
  goal: string;
  status: string;
  current_step: string | null;
  trace: TraceStep[];
  result: string | null;
  created_at: string;
  updated_at: string;
}

interface IngestResponse {
  status: string;
  result: string;
}

interface PreflightCheck {
  name: string;
  ok: boolean;
  required: boolean;
  detail: string;
}

interface PreflightResponse {
  ok: boolean;
  required_failed: number;
  optional_failed: number;
  checks: PreflightCheck[];
}

interface VersionMetadata {
  name: string;
  version: string;
  environment: string;
  reported_at: string;
  build_sha: string;
  build_ref: string;
  build_time: string;
  image_tag: string;
}

interface MetricsResponse {
  jobs_total: number;
  jobs_active: number;
  jobs_terminal: number;
  jobs_by_status: Record<string, number>;
  audit_events_total: number;
  runtime: {
    process_uptime_seconds: number;
    counters: Record<string, number>;
    histograms: Record<string, { count: number; sum: number; max: number; p95: number }>;
  };
}

interface SloObjective {
  name: string;
  ok: boolean;
  target: string;
  value: number;
  detail: string;
}

interface SloResponse {
  ok: boolean;
  failed: number;
  objectives: SloObjective[];
}

interface BackupSummary {
  backup_id: string;
  size_bytes: number;
  sha256: string;
  encrypted: boolean;
  created_at?: string;
}

interface BackupCreateResponse extends BackupSummary {
  path: string;
  manifest: {
    database: {
      investigation_jobs: number;
      audit_events: number;
    };
    include_files: boolean;
    secrets_included: boolean;
  };
}

interface AuditEvent {
  id: string;
  actor: string;
  action: string;
  resource_type: string;
  resource_id: string | null;
  event_hash: string;
  created_at: string;
}

interface AuditVerifyResponse {
  ok: boolean;
  events_checked: number;
  latest_hash?: string;
  failure?: string;
  event_id?: string;
}

interface JobHistoryItem {
  id: string;
  goal: string;
  status: string;
  result: string | null;
  updated_at: string;
}

const API_BASE = 'http://localhost:8000';
const ACTIVE_STATUSES = new Set(['QUEUED', 'PLANNING', 'RUNNING', 'REFLECTING']);
const HISTORY_KEY = 'autoops.jobHistory';

const quickActions = [
  {
    label: 'PDF Summary',
    goal: 'Summarize /mac/downloads/AI_Engineer_Intern_JD.pdf',
  },
  {
    label: 'Ask PDF',
    goal: 'Ask /mac/downloads/AI_Engineer_Intern_JD.pdf what the key requirements are',
  },
  {
    label: 'Unread Gmail',
    goal: 'Fetch my unread Gmail from today and summarize it.',
  },
  {
    label: 'System Check',
    goal: 'Check my local system stats and tell me if anything looks risky.',
  },
  {
    label: 'Code Search',
    goal: 'Search this project for every place JWT auth is used and explain the flow.',
  },
];

const pathExamples = [
  '/mac/downloads/file.pdf',
  '/mac/documents/notes.pdf',
  '/mac/desktop/report.pdf',
  '/app/inbox/paper.pdf',
];

function statusTone(status: string) {
  if (status === 'SUCCESS') return 'success';
  if (status === 'FAILED' || status === 'AUTH ERROR') return 'danger';
  if (status === 'RUNNING') return 'running';
  if (status === 'PLANNING' || status === 'REFLECTING') return 'info';
  return 'neutral';
}

function healthTone(ok: boolean | undefined) {
  if (ok === true) return 'success';
  if (ok === false) return 'danger';
  return 'info';
}

function isJobResponse(item: JobResponse | JobHistoryItem): item is JobResponse {
  return 'trace' in item && Array.isArray(item.trace);
}

function formatNumber(value: number) {
  return new Intl.NumberFormat('en-US', { maximumFractionDigits: 2 }).format(value);
}

function formatUptime(seconds: number) {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  return `${Math.round(seconds / 3600)}h`;
}

function loadHistory(): JobHistoryItem[] {
  try {
    return JSON.parse(localStorage.getItem(HISTORY_KEY) || '[]');
  } catch {
    return [];
  }
}

function saveHistory(items: JobHistoryItem[]) {
  localStorage.setItem(HISTORY_KEY, JSON.stringify(items.slice(0, 12)));
}

function renderReport(text: string) {
  return text.split('\n').map((line, index) => {
    const trimmed = line.trim();
    if (!trimmed) return <div key={index} className="report-space" />;
    if (/^#{1,3}\s+/.test(trimmed)) {
      return <h3 key={index}>{trimmed.replace(/^#{1,3}\s+/, '')}</h3>;
    }
    if (/^\d+\.\s+/.test(trimmed)) {
      return <p key={index} className="report-list">{trimmed}</p>;
    }
    if (/^[-*]\s+/.test(trimmed)) {
      return <p key={index} className="report-list">{trimmed.replace(/^[-*]\s+/, '• ')}</p>;
    }
    return <p key={index}>{line}</p>;
  });
}

function isRateLimitError(exc: unknown) {
  return (
    typeof exc === 'object'
    && exc !== null
    && 'response' in exc
    && typeof (exc as { response?: { status?: unknown } }).response?.status === 'number'
    && (exc as { response?: { status?: number } }).response?.status === 429
  );
}

export default function App() {
  const [goal, setGoal] = useState('Summarize /mac/downloads/AI_Engineer_Intern_JD.pdf');
  const [apiToken, setApiToken] = useState<string | null>(null);
  const [userRole, setUserRole] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [status, setStatus] = useState('IDLE');
  const [currentStep, setCurrentStep] = useState<string | null>(null);
  const [trace, setTrace] = useState<TraceStep[]>([]);
  const [result, setResult] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showTrace, setShowTrace] = useState(false);
  const [history, setHistory] = useState<JobHistoryItem[]>(loadHistory);
  const [filePath, setFilePath] = useState('/mac/downloads/AI_Engineer_Intern_JD.pdf');
  const [ingestStatus, setIngestStatus] = useState<string | null>(null);
  const [preflight, setPreflight] = useState<PreflightResponse | null>(null);
  const [preflightError, setPreflightError] = useState<string | null>(null);
  const [backups, setBackups] = useState<BackupSummary[]>([]);
  const [backupStatus, setBackupStatus] = useState<string | null>(null);
  const [encryptBackup, setEncryptBackup] = useState(false);
  const [auditEvents, setAuditEvents] = useState<AuditEvent[]>([]);
  const [auditVerify, setAuditVerify] = useState<AuditVerifyResponse | null>(null);
  const [auditStatus, setAuditStatus] = useState<string | null>(null);
  const [version, setVersion] = useState<VersionMetadata | null>(null);
  const [metrics, setMetrics] = useState<MetricsResponse | null>(null);
  const [slo, setSlo] = useState<SloResponse | null>(null);
  const [liveJobs, setLiveJobs] = useState<JobResponse[]>([]);
  const [opsError, setOpsError] = useState<string | null>(null);
  const [lastOpsRefresh, setLastOpsRefresh] = useState<string | null>(null);
  const intervalRef = useRef<number | null>(null);

  const isActive = ACTIVE_STATUSES.has(status);
  const tone = statusTone(status);

  const toolCalls = useMemo(
    () => trace.flatMap(step => step.tool_calls ?? []),
    [trace],
  );

  const importantPreflight = useMemo(
    () => preflight?.checks
      .filter(check => !check.ok || check.required || check.name.startsWith('rate_limit'))
      .slice(0, 8) ?? [],
    [preflight],
  );

  const recentJobs = useMemo(
    () => (liveJobs.length > 0 ? liveJobs : history),
    [history, liveJobs],
  );

  const statusRows = useMemo(
    () => Object.entries(metrics?.jobs_by_status ?? {}).sort(([left], [right]) => left.localeCompare(right)),
    [metrics],
  );

  const p95LatencyMs = useMemo(() => {
    const values = Object.entries(metrics?.runtime.histograms ?? {})
      .filter(([key]) => key.startsWith('autoops_api_request_duration'))
      .map(([, value]) => value.p95 * 1000);
    return values.length ? Math.max(...values) : 0;
  }, [metrics]);

  const refreshOps = useCallback(async () => {
    try {
      setOpsError(null);
      const [versionRes, metricsRes, sloRes] = await Promise.all([
        axios.get<VersionMetadata>(`${API_BASE}/version`),
        axios.get<MetricsResponse>(`${API_BASE}/metrics`),
        axios.get<SloResponse>(`${API_BASE}/slo`),
      ]);
      setVersion(versionRes.data);
      setMetrics(metricsRes.data);
      setSlo(sloRes.data);
      setLastOpsRefresh(new Date().toLocaleTimeString());

      if (apiToken) {
        const jobsRes = await axios.get<JobResponse[]>(`${API_BASE}/jobs?limit=12`, {
          headers: { Authorization: `Bearer ${apiToken}` },
        });
        setLiveJobs(jobsRes.data);
      }
    } catch {
      setOpsError('Could not refresh the operations dashboard.');
    }
  }, [apiToken]);

  const refreshBackups = useCallback(async () => {
    if (!apiToken) return;
    try {
      const res = await axios.get<{ backups: BackupSummary[] }>(
        `${API_BASE}/backups`,
        { headers: { Authorization: `Bearer ${apiToken}` } },
      );
      setBackups(res.data.backups);
    } catch {
      setBackupStatus('Could not load backups.');
    }
  }, [apiToken]);

  const refreshAudit = useCallback(async () => {
    if (!apiToken) return;
    try {
      setAuditStatus(null);
      const [eventsRes, verifyRes] = await Promise.all([
        axios.get<AuditEvent[]>(`${API_BASE}/audit?limit=6`, {
          headers: { Authorization: `Bearer ${apiToken}` },
        }),
        axios.get<AuditVerifyResponse>(`${API_BASE}/audit/verify`, {
          headers: { Authorization: `Bearer ${apiToken}` },
        }),
      ]);
      setAuditEvents(eventsRes.data);
      setAuditVerify(verifyRes.data);
    } catch {
      setAuditStatus('Could not load audit trail.');
    }
  }, [apiToken]);

  useEffect(() => {
    (async () => {
      try {
        const res = await axios.post<TokenResponse>(`${API_BASE}/token`, {
          username: 'admin',
          password: 'password',
        });
        setApiToken(res.data.access_token);
        setUserRole(res.data.role);
      } catch {
        setStatus('AUTH ERROR');
        setError('Could not authenticate with the local API.');
      }
    })();
  }, []);

  useEffect(() => {
    refreshPreflight();
  }, []);

  useEffect(() => {
    refreshOps();
    const opsInterval = window.setInterval(refreshOps, 15000);
    return () => window.clearInterval(opsInterval);
  }, [refreshOps]);

  useEffect(() => {
    if (apiToken) {
      refreshBackups();
      refreshAudit();
      refreshOps();
    }
  }, [apiToken, refreshBackups, refreshAudit, refreshOps]);

  useEffect(() => {
    if (intervalRef.current) {
      clearInterval(intervalRef.current);
      intervalRef.current = null;
    }

    if (!apiToken || !jobId || !ACTIVE_STATUSES.has(status)) return;

    intervalRef.current = window.setInterval(async () => {
      try {
        const res = await axios.get<JobResponse>(`${API_BASE}/jobs/${jobId}`, {
          headers: { Authorization: `Bearer ${apiToken}` },
        });
        const data = res.data;
        setStatus(data.status);
        setCurrentStep(data.current_step);
        setTrace(data.trace ?? []);
        setResult(data.result);

        if (!ACTIVE_STATUSES.has(data.status)) {
          const nextHistory = [
            {
              id: data.id,
              goal: data.goal,
              status: data.status,
              result: data.result,
              updated_at: data.updated_at,
            },
            ...history.filter(item => item.id !== data.id),
          ];
          setHistory(nextHistory);
          saveHistory(nextHistory);
          refreshOps();
        }
      } catch {
        setError('Polling failed. The API may still be running; retrying on the next tick.');
      }
    }, status === 'RUNNING' ? 1000 : 1600);

    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [apiToken, history, jobId, refreshOps, status]);

  async function submitInvestigation(nextGoal = goal) {
    if (!apiToken) {
      setStatus('AUTH ERROR');
      setError('No API token is available yet.');
      return;
    }

    setGoal(nextGoal);
    setJobId(null);
    setTrace([]);
    setResult(null);
    setCurrentStep(null);
    setError(null);
    setStatus('QUEUED');

    try {
      const res = await axios.post<InvestigateResponse>(
        `${API_BASE}/investigate`,
        { goal: nextGoal },
        { headers: { Authorization: `Bearer ${apiToken}` } },
      );
      setJobId(res.data.job_id);
      setStatus(res.data.status);
    } catch (exc) {
      setStatus('FAILED');
      if (isRateLimitError(exc)) {
        setError('Rate limit reached. Wait a moment, then retry.');
      } else {
        setError('Could not submit the job to the local API.');
      }
    }
  }

  async function ingestPath() {
    if (!apiToken) return;
    setIngestStatus('Indexing document…');
    try {
      const res = await axios.post<IngestResponse>(
        `${API_BASE}/ingest`,
        { file_path: filePath, doc_type: 'pdf' },
        { headers: { Authorization: `Bearer ${apiToken}` } },
      );
      setIngestStatus(res.data.result);
    } catch (exc) {
      if (isRateLimitError(exc)) {
        setIngestStatus('Rate limit reached. Wait a moment, then retry.');
      } else {
        setIngestStatus('Ingest failed. Check that the path is mounted and allowed.');
      }
    }
  }

  async function refreshPreflight() {
    try {
      setPreflightError(null);
      const res = await axios.get<PreflightResponse>(`${API_BASE}/preflight`);
      setPreflight(res.data);
    } catch {
      setPreflightError('Preflight failed. Check whether the API is reachable.');
    }
  }

  async function createLocalBackup() {
    if (!apiToken) return;
    setBackupStatus('Creating backup…');
    try {
      const res = await axios.post<BackupCreateResponse>(
        `${API_BASE}/backups`,
        { include_files: true, encrypt: encryptBackup },
        { headers: { Authorization: `Bearer ${apiToken}` } },
      );
      setBackupStatus(
        `Created ${res.data.backup_id} with ${res.data.manifest.database.investigation_jobs} jobs and ${res.data.manifest.database.audit_events} audit events${res.data.encrypted ? ' encrypted' : ''}.`,
      );
      await refreshBackups();
      await refreshAudit();
    } catch (exc) {
      if (isRateLimitError(exc)) {
        setBackupStatus('Rate limit reached. Wait a moment, then retry.');
      } else {
        setBackupStatus('Backup failed. Check API logs and preflight diagnostics.');
      }
    }
  }

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">A</span>
          <div>
            <h1>AutoOps</h1>
            <p>Local agent workspace</p>
          </div>
        </div>

        <section className="side-section">
          <h2>Workflows</h2>
          <div className="quick-list">
            {quickActions.map(action => (
              <button
                key={action.label}
                type="button"
                onClick={() => setGoal(action.goal)}
                className="quick-button"
              >
                {action.label}
              </button>
            ))}
          </div>
        </section>

        <section className="side-section">
          <h2>Recent Jobs</h2>
          {history.length === 0 ? (
            <p className="muted">No completed jobs yet.</p>
          ) : (
            <div className="history-list">
              {history.map(item => (
                <button
                  key={item.id}
                  type="button"
                  className="history-item"
                  onClick={() => {
                    setGoal(item.goal);
                    setJobId(item.id);
                    setStatus(item.status);
                    setResult(item.result);
                    setTrace([]);
                    setCurrentStep(null);
                  }}
                >
                  <span>{item.goal}</span>
                  <strong>{item.status}</strong>
                </button>
              ))}
            </div>
          )}
        </section>
      </aside>

      <section className="workspace">
        <header className="topbar">
          <div>
            <h2>Agentic DevOps & Personal Assistant</h2>
            <p>Run local AI workflows across files, PDFs, Gmail, code, and system checks.</p>
          </div>
          <div className="topbar-badges">
            {userRole && <span className="status-pill neutral">{userRole}</span>}
            <span className={`status-pill ${tone}`}>{status}</span>
          </div>
        </header>

        <section className="dashboard-band">
          <div className="dashboard-heading">
            <div>
              <h3>Operations Dashboard</h3>
              <p>Release, reliability, jobs, audit, and recovery posture.</p>
            </div>
            <button type="button" onClick={refreshOps}>Refresh</button>
          </div>
          {opsError && <p className="error">{opsError}</p>}
          <div className="dashboard-grid">
            <article className="metric-card">
              <span>Release</span>
              <strong>{version?.version ?? 'unknown'}</strong>
              <p>{version?.environment ?? 'checking'} · {version?.build_ref ?? 'unknown ref'}</p>
              <code>{version?.build_sha?.slice(0, 12) ?? 'no-build-sha'}</code>
            </article>
            <article className={`metric-card ${healthTone(slo?.ok)}`}>
              <span>SLO Health</span>
              <strong>{slo?.ok ? 'Passing' : slo ? `${slo.failed} failed` : 'Checking'}</strong>
              <p>{slo?.objectives.length ?? 0} objectives tracked</p>
              <code>{lastOpsRefresh ? `refreshed ${lastOpsRefresh}` : 'pending refresh'}</code>
            </article>
            <article className="metric-card">
              <span>Jobs</span>
              <strong>{metrics ? formatNumber(metrics.jobs_total) : '0'}</strong>
              <p>{metrics ? `${metrics.jobs_active} active · ${metrics.jobs_terminal} terminal` : 'waiting for metrics'}</p>
              <code>{statusRows.length} statuses</code>
            </article>
            <article className="metric-card">
              <span>Runtime</span>
              <strong>{metrics ? formatUptime(metrics.runtime.process_uptime_seconds) : '0s'}</strong>
              <p>{formatNumber(p95LatencyMs)}ms p95 API latency</p>
              <code>{metrics ? `${metrics.audit_events_total} audit events` : 'audit pending'}</code>
            </article>
          </div>

          <div className="ops-grid">
            <section className="ops-panel">
              <div className="mini-header">
                <h4>Recent Jobs</h4>
                <span>{recentJobs.length}</span>
              </div>
              <div className="job-table">
                {recentJobs.slice(0, 6).map(item => (
                  <button
                    key={item.id}
                    type="button"
                    className="job-row"
                    onClick={() => {
                      setGoal(item.goal);
                      setJobId(item.id);
                      setStatus(item.status);
                      setResult(item.result);
                      setTrace(isJobResponse(item) ? item.trace : []);
                      setCurrentStep(isJobResponse(item) ? item.current_step : null);
                    }}
                  >
                    <span>{item.goal}</span>
                    <strong className={statusTone(item.status)}>{item.status}</strong>
                  </button>
                ))}
                {recentJobs.length === 0 && <p className="muted">No jobs reported yet.</p>}
              </div>
            </section>

            <section className="ops-panel">
              <div className="mini-header">
                <h4>SLO Objectives</h4>
                <span>{slo?.failed ?? 0} failed</span>
              </div>
              <div className="slo-list">
                {(slo?.objectives ?? []).map(objective => (
                  <article key={objective.name} className={`slo-row ${objective.ok ? 'success' : 'danger'}`}>
                    <div>
                      <strong>{objective.name}</strong>
                      <span>{objective.target}</span>
                    </div>
                    <code>{formatNumber(objective.value)}</code>
                  </article>
                ))}
                {!slo && <p className="muted">Waiting for SLO data.</p>}
              </div>
            </section>

            <section className="ops-panel">
              <div className="mini-header">
                <h4>Job Status</h4>
                <span>{statusRows.length}</span>
              </div>
              <div className="status-bars">
                {statusRows.map(([name, count]) => (
                  <div key={name} className="status-bar">
                    <span>{name}</span>
                    <strong>{count}</strong>
                  </div>
                ))}
                {statusRows.length === 0 && <p className="muted">No job metrics yet.</p>}
              </div>
            </section>

            <section className="ops-panel">
              <div className="mini-header">
                <h4>Controls</h4>
                <span>{preflight?.ok ? 'ready' : 'review'}</span>
              </div>
              <div className="control-list">
                <button type="button" onClick={refreshPreflight}>Preflight</button>
                <button type="button" onClick={refreshAudit} disabled={!apiToken}>Audit</button>
                <button type="button" onClick={refreshBackups} disabled={!apiToken}>Backups</button>
                <button type="button" onClick={() => submitInvestigation('Check local system stats and summarize operational risk.')}>
                  System Check
                </button>
              </div>
            </section>
          </div>
        </section>

        <section className="panel command-panel">
          <label htmlFor="goal">Goal</label>
          <div className="command-row">
            <textarea
              id="goal"
              value={goal}
              onChange={event => setGoal(event.target.value)}
              placeholder="Ask AutoOps what to do…"
              rows={3}
              onKeyDown={event => {
                if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
                  submitInvestigation();
                }
              }}
            />
            <button type="button" onClick={() => submitInvestigation()} disabled={isActive}>
              {isActive ? 'Running' : 'Run'}
            </button>
          </div>
          <div className="hint-row">
            <span>Use mounted paths like</span>
            {pathExamples.map(example => <code key={example}>{example}</code>)}
          </div>
        </section>

        <section className="grid">
          <div className="panel">
            <h3>PDF & Document Flow</h3>
            <p className="muted">Summarize, inspect, ask questions, or index mounted PDFs.</p>
            <input
              value={filePath}
              onChange={event => setFilePath(event.target.value)}
              placeholder="/mac/downloads/file.pdf"
            />
            <div className="button-row">
              <button type="button" onClick={() => submitInvestigation(`Summarize ${filePath}`)}>
                Summarize
              </button>
              <button type="button" onClick={() => submitInvestigation(`Inspect ${filePath}`)}>
                Inspect
              </button>
              <button type="button" onClick={ingestPath}>
                Index
              </button>
            </div>
            {ingestStatus && <p className="notice">{ingestStatus}</p>}
          </div>

          <div className="panel">
            <h3>Live Progress</h3>
            <div className={`progress-card ${tone}`}>
              <strong>{currentStep || (isActive ? 'Waiting for worker update…' : 'Idle')}</strong>
              {jobId && <span>Job {jobId.slice(0, 8)}…</span>}
            </div>
            <div className="stats-row">
              <span>{toolCalls.length} tool calls</span>
              <span>{trace.length} trace messages</span>
            </div>
            {error && <p className="error">{error}</p>}
          </div>
        </section>

        <section className="panel">
          <div className="panel-header">
            <h3>Runtime Preflight</h3>
            <button type="button" onClick={refreshPreflight}>Refresh</button>
          </div>
          {preflight ? (
            <>
              <div className={`progress-card ${preflight.ok ? 'success' : 'danger'}`}>
                <strong>{preflight.ok ? 'Required systems ready' : 'Required systems need attention'}</strong>
                <span>
                  {preflight.required_failed} required failures · {preflight.optional_failed} optional warnings
                </span>
              </div>
              <div className="check-grid">
                {importantPreflight.map(check => (
                  <div key={check.name} className={`check-item ${check.ok ? 'success' : check.required ? 'danger' : 'info'}`}>
                    <strong>{check.name}</strong>
                    <span>{check.detail}</span>
                  </div>
                ))}
              </div>
            </>
          ) : (
            <p className="muted">{preflightError || 'Checking runtime configuration…'}</p>
          )}
          {preflightError && <p className="error">{preflightError}</p>}
        </section>

        <section className="panel">
          <div className="panel-header">
            <h3>Backups</h3>
            <div className="button-row compact">
              <button type="button" onClick={refreshBackups}>Refresh</button>
              <button type="button" onClick={createLocalBackup} disabled={!apiToken}>
                Create
              </button>
            </div>
          </div>
          <label className="inline-check">
            <input
              type="checkbox"
              checked={encryptBackup}
              onChange={event => setEncryptBackup(event.target.checked)}
            />
            <span>Encrypt new backup</span>
          </label>
          <div className="progress-card info">
            <strong>{backups.length} local backup{backups.length === 1 ? '' : 's'}</strong>
            <span>Restore is dry-run by default unless explicitly enabled on the API.</span>
          </div>
          {backupStatus && <p className="notice">{backupStatus}</p>}
          {backups.length > 0 && (
            <div className="backup-list">
              {backups.slice(0, 5).map(backup => (
                <article key={backup.backup_id} className="backup-item">
                  <strong>{backup.backup_id}</strong>
                  <span>
                    {backup.encrypted ? 'encrypted' : 'plain'} · {Math.max(1, Math.round(backup.size_bytes / 1024))} KB · {backup.sha256.slice(0, 12)}
                  </span>
                </article>
              ))}
            </div>
          )}
        </section>

        <section className="panel">
          <div className="panel-header">
            <h3>Audit Trail</h3>
            <button type="button" onClick={refreshAudit} disabled={!apiToken}>Refresh</button>
          </div>
          <div className={`progress-card ${auditVerify?.ok ? 'success' : auditVerify ? 'danger' : 'info'}`}>
            <strong>{auditVerify?.ok ? 'Audit chain verified' : auditVerify ? 'Audit chain needs review' : 'Audit chain pending'}</strong>
            <span>
              {auditVerify
                ? `${auditVerify.events_checked} events checked${auditVerify.latest_hash ? ` · ${auditVerify.latest_hash.slice(0, 12)}` : ''}`
                : 'Waiting for audit verification'}
            </span>
          </div>
          {auditStatus && <p className="error">{auditStatus}</p>}
          {auditEvents.length > 0 && (
            <div className="audit-list">
              {auditEvents.map(event => (
                <article key={event.id} className="audit-item">
                  <div>
                    <strong>{event.action}</strong>
                    <span>{event.actor} · {event.resource_type}{event.resource_id ? ` · ${event.resource_id}` : ''}</span>
                  </div>
                  <code>{event.event_hash.slice(0, 10)}</code>
                </article>
              ))}
            </div>
          )}
        </section>

        {result && (
          <section className="panel report-panel">
            <div className="panel-header">
              <h3>Final Report</h3>
              <button type="button" onClick={() => navigator.clipboard?.writeText(result)}>
                Copy
              </button>
            </div>
            <div className="report-body">{renderReport(result)}</div>
          </section>
        )}

        {trace.length > 0 && (
          <section className="panel trace-panel">
            <div className="panel-header">
              <h3>Reasoning Trace</h3>
              <button type="button" onClick={() => setShowTrace(!showTrace)}>
                {showTrace ? 'Hide' : 'Show'}
              </button>
            </div>
            {showTrace && (
              <div className="trace-list">
                {trace.map((step, index) => (
                  <article key={`${step.type}-${index}`} className={`trace-item ${step.type}`}>
                    <header>
                      <span>{step.type}</span>
                    </header>
                    {step.content && <p>{step.content}</p>}
                    {step.tool_calls && step.tool_calls.length > 0 && (
                      <div className="tool-list">
                        {step.tool_calls.map((tool, toolIndex) => (
                          <code key={`${tool.name}-${toolIndex}`}>
                            {tool.name}({JSON.stringify(tool.args)})
                          </code>
                        ))}
                      </div>
                    )}
                  </article>
                ))}
              </div>
            )}
          </section>
        )}
      </section>
    </main>
  );
}
