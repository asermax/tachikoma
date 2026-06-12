CREATE TABLE `detached_processes` (
	`id` text PRIMARY KEY NOT NULL,
	`name` text NOT NULL,
	`command` text NOT NULL,
	`cwd` text NOT NULL,
	`pid` integer NOT NULL,
	`status` text NOT NULL,
	`exit_code` integer,
	`stop_reason` text,
	`stdout_path` text NOT NULL,
	`stderr_path` text NOT NULL,
	`memory_limit_mb` integer,
	`started_at` integer NOT NULL,
	`exited_at` integer
);
--> statement-breakpoint
CREATE INDEX `ix_detached_processes_status` ON `detached_processes` (`status`);--> statement-breakpoint
CREATE TABLE `task_definitions` (
	`id` text PRIMARY KEY NOT NULL,
	`name` text NOT NULL,
	`schedule` text NOT NULL,
	`task_type` text NOT NULL,
	`prompt` text NOT NULL,
	`enabled` integer DEFAULT true NOT NULL,
	`last_fired_at` integer,
	`since` integer NOT NULL,
	`created_at` integer NOT NULL
);
--> statement-breakpoint
CREATE TABLE `task_instances` (
	`id` text PRIMARY KEY NOT NULL,
	`definition_id` text,
	`task_type` text NOT NULL,
	`status` text NOT NULL,
	`prompt` text NOT NULL,
	`scheduled_for` integer NOT NULL,
	`started_at` integer,
	`completed_at` integer,
	`result` text,
	`user_response` text,
	`updated_at` integer NOT NULL,
	`created_at` integer NOT NULL,
	FOREIGN KEY (`definition_id`) REFERENCES `task_definitions`(`id`) ON UPDATE no action ON DELETE no action
);
--> statement-breakpoint
CREATE INDEX `ix_task_instances_status` ON `task_instances` (`status`);--> statement-breakpoint
CREATE INDEX `ix_task_instances_task_type` ON `task_instances` (`task_type`);--> statement-breakpoint
CREATE TABLE `workflow_states` (
	`id` text PRIMARY KEY NOT NULL,
	`skill_name` text NOT NULL,
	`workflow_name` text NOT NULL,
	`current_step` text,
	`step_states` text NOT NULL,
	`definition_snapshot` text NOT NULL,
	`scratchpad_path` text NOT NULL,
	`deleted_at` integer,
	`created_at` integer NOT NULL,
	`updated_at` integer NOT NULL
);
--> statement-breakpoint
CREATE INDEX `ix_workflow_states_skill_name` ON `workflow_states` (`skill_name`);--> statement-breakpoint
CREATE INDEX `ix_workflow_states_workflow_name` ON `workflow_states` (`workflow_name`);--> statement-breakpoint
CREATE INDEX `ix_workflow_states_active_lookup` ON `workflow_states` (`skill_name`,`workflow_name`);