DROP TABLE `sessions`;--> statement-breakpoint
DROP TABLE `channel_messages`;--> statement-breakpoint
CREATE TABLE `channel_messages` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`channel` text NOT NULL,
	`message_id` text NOT NULL,
	`tree_entry_id` text NOT NULL,
	`branch_id` text NOT NULL,
	`direction` text NOT NULL,
	`created_at` integer NOT NULL
);
--> statement-breakpoint
CREATE UNIQUE INDEX `ux_channel_messages_channel_message` ON `channel_messages` (`channel`,`message_id`);--> statement-breakpoint
CREATE INDEX `ix_channel_messages_branch` ON `channel_messages` (`branch_id`);
