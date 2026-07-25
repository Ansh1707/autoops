from enum import StrEnum


class JobStatus(StrEnum):
    QUEUED = "QUEUED"
    PLANNING = "PLANNING"
    RUNNING = "RUNNING"
    REFLECTING = "REFLECTING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


TERMINAL_STATUSES = {JobStatus.SUCCESS.value, JobStatus.FAILED.value}
