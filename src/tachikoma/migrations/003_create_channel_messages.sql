-- Migration 003: create_channel_messages
--
-- Maps platform-specific message IDs (e.g., Telegram message_id) to internal
-- conversation sessions, enabling reaction-based session routing.
--
-- Uses IF NOT EXISTS because create_all() may have already created this table
-- from the ORM model (existing-install upgrade path).

CREATE TABLE IF NOT EXISTS channel_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    channel TEXT NOT NULL,
    direction TEXT NOT NULL,
    external_id TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_channel_messages_channel_external_id
    ON channel_messages(channel, external_id);

CREATE INDEX IF NOT EXISTS ix_channel_messages_session_id
    ON channel_messages(session_id);
