-- Migration 004: add_workflow_id_and_pending_handoff
--
-- Adds workflow_id to task_instances (linking step tasks to their workflow)
-- and pending_handoff to workflow_states (inter-step hand-off message storage).
--
-- Uses IF NOT EXISTS because create_all() may have already created these columns
-- from the ORM model (existing-install upgrade path).

ALTER TABLE task_instances ADD COLUMN workflow_id VARCHAR DEFAULT NULL;

CREATE INDEX IF NOT EXISTS ix_task_instances_workflow_id ON task_instances(workflow_id);

ALTER TABLE workflow_states ADD COLUMN pending_handoff VARCHAR DEFAULT NULL;
