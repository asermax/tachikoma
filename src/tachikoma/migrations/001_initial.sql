-- Migration 001: initial_schema
--
-- Frozen baseline. This file documents all schema changes that existed before the
-- tracked migration system was introduced. It is NEVER auto-executed — the runner
-- stamps it as applied without running these statements. For fresh installs,
-- create_all() already produces the current schema from ORM definitions.

ALTER TABLE sessions ADD COLUMN summary TEXT;

ALTER TABLE sessions ADD COLUMN last_resumed_at DATETIME;

ALTER TABLE sessions ADD COLUMN processed_at DATETIME;

CREATE TABLE session_resumptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    resumed_at DATETIME NOT NULL,
    previous_ended_at DATETIME NOT NULL
);

CREATE INDEX ix_session_resumptions_session_id ON session_resumptions(session_id);

CREATE TABLE session_context_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    owner TEXT NOT NULL,
    content TEXT NOT NULL
);

CREATE INDEX ix_session_context_entries_session_id ON session_context_entries(session_id);

ALTER TABLE task_definitions DROP COLUMN notify;

ALTER TABLE sessions ADD COLUMN error BOOLEAN DEFAULT 0;

ALTER TABLE sessions ADD COLUMN last_exchange TEXT;

ALTER TABLE session_context_entries ADD COLUMN metadata TEXT;

ALTER TABLE task_instances ADD COLUMN sdk_session_id TEXT;

ALTER TABLE task_instances ADD COLUMN user_response TEXT;

ALTER TABLE task_instances ADD COLUMN updated_at DATETIME;

ALTER TABLE task_definitions ADD COLUMN since DATETIME NOT NULL DEFAULT '2025-01-01T00:00:00+00:00';

ALTER TABLE detached_processes ADD COLUMN stop_reason TEXT;

ALTER TABLE workflow_states ADD COLUMN parent_workflow_id TEXT;

ALTER TABLE workflow_states ADD COLUMN parent_step_id TEXT;

ALTER TABLE workflow_states ADD COLUMN loop_state TEXT;

ALTER TABLE task_definitions ADD COLUMN skills TEXT NOT NULL DEFAULT '[]';

CREATE TABLE plugin_state (
    alias VARCHAR NOT NULL,
    installed_version VARCHAR,
    update_status VARCHAR DEFAULT 'unknown',
    available_version VARCHAR,
    last_checked_at DATETIME,
    diagnostic VARCHAR,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (alias)
);
