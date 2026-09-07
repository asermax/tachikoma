ALTER TABLE `channel_messages` ADD `label` text;--> statement-breakpoint
ALTER TABLE `channel_messages` ADD `in_conversation` integer DEFAULT true NOT NULL;