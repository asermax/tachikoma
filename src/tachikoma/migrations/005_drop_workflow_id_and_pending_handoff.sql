-- Migration 005: drop_workflow_id_and_pending_handoff
--
-- Reverses migration 004. DLT-176 (workflow tasks as background execution)
-- was rolled back; these columns are no longer used.
--
-- Uses IF EXISTS because fresh installs never ran migration 004.

DROP INDEX IF EXISTS ix_task_instances_workflow_id;

ALTER TABLE task_instances DROP COLUMN IF EXISTS workflow_id;

ALTER TABLE workflow_states DROP COLUMN IF EXISTS pending_handoff;
