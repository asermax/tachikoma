ALTER TABLE `workflow_states` ADD `parent_workflow_id` text;--> statement-breakpoint
ALTER TABLE `workflow_states` ADD `parent_step_id` text;--> statement-breakpoint
ALTER TABLE `workflow_states` ADD `loop_state` text;--> statement-breakpoint
CREATE INDEX `ix_workflow_states_parent` ON `workflow_states` (`parent_workflow_id`,`deleted_at`);