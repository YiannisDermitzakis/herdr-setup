-- Captured verbatim from `sqlite3 ~/.local/share/opencode/opencode.db ".schema session"`
-- on this machine, 2026-09-08 (opencode 1.18.5). See README.md in this
-- directory for what "captured" means here: the SCHEMA is a byte-for-byte
-- copy of a real CREATE TABLE statement; no row in this fixture set came
-- from that database. adapters/opencode reads six of these columns
-- (id, parent_id, directory, title, time_updated, time_archived); the rest
-- are carried here anyway because the rule (tests/fixtures/herdr/README.md)
-- is capture the schema verbatim, not trim it to what one caller happens to
-- need today.
CREATE TABLE `session` (
	`id` text PRIMARY KEY,
	`project_id` text NOT NULL,
	`parent_id` text,
	`slug` text NOT NULL,
	`directory` text NOT NULL,
	`title` text NOT NULL,
	`version` text NOT NULL,
	`share_url` text,
	`summary_additions` integer,
	`summary_deletions` integer,
	`summary_files` integer,
	`summary_diffs` text,
	`revert` text,
	`permission` text,
	`time_created` integer NOT NULL,
	`time_updated` integer NOT NULL,
	`time_compacting` integer,
	`time_archived` integer, `workspace_id` text, `path` text, `agent` text, `model` text, `cost` real DEFAULT 0 NOT NULL, `tokens_input` integer DEFAULT 0 NOT NULL, `tokens_output` integer DEFAULT 0 NOT NULL, `tokens_reasoning` integer DEFAULT 0 NOT NULL, `tokens_cache_read` integer DEFAULT 0 NOT NULL, `tokens_cache_write` integer DEFAULT 0 NOT NULL, `metadata` text,
	CONSTRAINT `fk_session_project_id_project_id_fk` FOREIGN KEY (`project_id`) REFERENCES `project`(`id`) ON DELETE CASCADE
);
CREATE INDEX `session_project_idx` ON `session` (`project_id`);
CREATE INDEX `session_parent_idx` ON `session` (`parent_id`);
CREATE INDEX `session_workspace_idx` ON `session` (`workspace_id`);
