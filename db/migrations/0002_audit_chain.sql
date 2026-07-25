-- AutoOps migration: 0002_audit_chain
-- Description: Create append-only audit events with hash-chain fields.
-- Direction: forward-only

CREATE TABLE IF NOT EXISTS audit_events (
    id VARCHAR PRIMARY KEY,
    actor VARCHAR NOT NULL DEFAULT 'system',
    action VARCHAR NOT NULL,
    resource_type VARCHAR NOT NULL,
    resource_id VARCHAR,
    request_id VARCHAR,
    metadata_json JSON DEFAULT '{}',
    previous_hash VARCHAR,
    event_hash VARCHAR NOT NULL,
    created_at TIMESTAMP
);
