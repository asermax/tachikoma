CREATE TABLE `app_state` (
	`namespace` text NOT NULL,
	`key` text NOT NULL,
	`value` text,
	`updated_at` integer NOT NULL,
	PRIMARY KEY(`namespace`, `key`)
);
--> statement-breakpoint
CREATE TABLE `sessions` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`channel` text NOT NULL,
	`pi_session_file` text,
	`summary` text,
	`last_exchange` text,
	`created_at` integer NOT NULL,
	`closed_at` integer,
	`last_resumed_at` integer,
	`post_processing_state` text
);
