ALTER TABLE `channel_messages` ADD `label` text;--> statement-breakpoint
ALTER TABLE `channel_messages` ADD `in_conversation` integer DEFAULT true NOT NULL;--> statement-breakpoint
CREATE INDEX `ix_channel_messages_entry_bottom` ON `channel_messages` (`channel`,`tree_entry_id`,`direction`,`in_conversation`,`created_at`,`id`);