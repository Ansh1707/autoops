-- AutoOps migration: 0004_schema_ledger
-- Description: Track applied schema migrations for drift detection.
-- Direction: forward-only

CREATE TABLE IF NOT EXISTS schema_migrations (
    version VARCHAR PRIMARY KEY,
    description VARCHAR NOT NULL,
    checksum VARCHAR NOT NULL,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
