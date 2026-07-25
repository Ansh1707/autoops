-- AutoOps migration: 0001_initial_jobs
-- Description: Create the investigation job table used by the agent worker.
-- Direction: forward-only

CREATE TABLE IF NOT EXISTS investigation_jobs (
    id VARCHAR PRIMARY KEY,
    goal TEXT NOT NULL,
    status VARCHAR,
    current_step VARCHAR,
    trace JSON DEFAULT '[]',
    result TEXT,
    created_at TIMESTAMP,
    updated_at TIMESTAMP
);
