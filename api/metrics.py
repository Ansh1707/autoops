"""Small in-process metrics registry for local AutoOps operations."""

from __future__ import annotations

import re
import math
import time
from collections import defaultdict
from dataclasses import dataclass
from threading import Lock

from sqlalchemy import func
from sqlalchemy.orm import Session

from api.models import AuditEvent, InvestigationJob
from api.status import TERMINAL_STATUSES


LabelSet = tuple[tuple[str, str], ...]
_METRIC_NAME_PATTERN = re.compile(r"[^a-zA-Z0-9_:]")


@dataclass(frozen=True)
class JobsMetrics:
    jobs_total: int
    jobs_active: int
    jobs_terminal: int
    jobs_by_status: dict[str, int]
    audit_events_total: int


class MetricsRegistry:
    def __init__(self) -> None:
        self._lock = Lock()
        self._counters: dict[tuple[str, LabelSet], float] = defaultdict(float)
        self._histograms: dict[tuple[str, LabelSet], list[float]] = defaultdict(list)
        self.started_at = time.time()

    def increment(self, name: str, value: float = 1.0, **labels: str) -> None:
        label_set = _label_set(labels)
        with self._lock:
            self._counters[(name, label_set)] += value

    def observe(self, name: str, value: float, **labels: str) -> None:
        label_set = _label_set(labels)
        with self._lock:
            self._histograms[(name, label_set)].append(value)

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._histograms.clear()
            self.started_at = time.time()

    def snapshot(self) -> dict:
        with self._lock:
            counters = {
                _series_key(name, labels): value
                for (name, labels), value in self._counters.items()
            }
            histograms = {
                _series_key(name, labels): {
                    "count": len(values),
                    "sum": sum(values),
                    "max": max(values) if values else 0.0,
                    "p95": _percentile(values, 0.95),
                }
                for (name, labels), values in self._histograms.items()
            }
        return {
            "process_uptime_seconds": max(0.0, time.time() - self.started_at),
            "counters": counters,
            "histograms": histograms,
        }

    def prometheus_text(self, job_metrics: JobsMetrics) -> str:
        lines = [
            "# HELP autoops_process_uptime_seconds Seconds since the API metrics registry started.",
            "# TYPE autoops_process_uptime_seconds gauge",
            f"autoops_process_uptime_seconds {max(0.0, time.time() - self.started_at):.6f}",
            "# HELP autoops_jobs_total Total jobs by status.",
            "# TYPE autoops_jobs_total gauge",
        ]
        for status, count in sorted(job_metrics.jobs_by_status.items()):
            lines.append(f'autoops_jobs_total{{status="{_escape_label(status)}"}} {count}')
        lines.extend([
            "# HELP autoops_jobs_active Active non-terminal jobs.",
            "# TYPE autoops_jobs_active gauge",
            f"autoops_jobs_active {job_metrics.jobs_active}",
            "# HELP autoops_jobs_terminal Terminal jobs.",
            "# TYPE autoops_jobs_terminal gauge",
            f"autoops_jobs_terminal {job_metrics.jobs_terminal}",
            "# HELP autoops_audit_events_total Total audit events stored.",
            "# TYPE autoops_audit_events_total gauge",
            f"autoops_audit_events_total {job_metrics.audit_events_total}",
        ])

        with self._lock:
            counters = list(self._counters.items())
            histograms = list(self._histograms.items())

        emitted_counter_types: set[str] = set()
        for (name, labels), value in sorted(counters, key=lambda item: (item[0][0], item[0][1])):
            metric_name = _metric_name(name)
            if metric_name not in emitted_counter_types:
                lines.append(f"# TYPE {metric_name} counter")
                emitted_counter_types.add(metric_name)
            lines.append(f"{metric_name}{_labels_text(labels)} {value:.0f}")

        emitted_histogram_types: set[str] = set()
        for (name, labels), values in sorted(histograms, key=lambda item: (item[0][0], item[0][1])):
            metric_name = _metric_name(name)
            if metric_name not in emitted_histogram_types:
                lines.append(f"# TYPE {metric_name}_seconds summary")
                emitted_histogram_types.add(metric_name)
            label_text = _labels_text(labels)
            lines.append(f"{metric_name}_seconds_count{label_text} {len(values)}")
            lines.append(f"{metric_name}_seconds_sum{label_text} {sum(values):.6f}")
            lines.append(f"{metric_name}_seconds_max{label_text} {(max(values) if values else 0.0):.6f}")

        return "\n".join(lines) + "\n"


metrics_registry = MetricsRegistry()


def job_metrics(db: Session) -> JobsMetrics:
    rows = (
        db.query(InvestigationJob.status, func.count(InvestigationJob.id))
        .group_by(InvestigationJob.status)
        .all()
    )
    jobs_by_status = {status: count for status, count in rows}
    total_jobs = sum(jobs_by_status.values())
    terminal_jobs = sum(
        count for status, count in jobs_by_status.items()
        if status in TERMINAL_STATUSES
    )
    audit_events_total = db.query(func.count(AuditEvent.id)).scalar() or 0
    return JobsMetrics(
        jobs_total=total_jobs,
        jobs_active=total_jobs - terminal_jobs,
        jobs_terminal=terminal_jobs,
        jobs_by_status=jobs_by_status,
        audit_events_total=audit_events_total,
    )


def _label_set(labels: dict[str, str]) -> LabelSet:
    return tuple(sorted((str(key), str(value)) for key, value in labels.items()))


def _series_key(name: str, labels: LabelSet) -> str:
    if not labels:
        return name
    joined = ",".join(f"{key}={value}" for key, value in labels)
    return f"{name}{{{joined}}}"


def _metric_name(name: str) -> str:
    return _METRIC_NAME_PATTERN.sub("_", name)


def _labels_text(labels: LabelSet) -> str:
    if not labels:
        return ""
    return "{" + ",".join(f'{key}="{_escape_label(value)}"' for key, value in labels) + "}"


def _escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(len(ordered) * percentile) - 1))
    return ordered[index]
