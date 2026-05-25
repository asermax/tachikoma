-- Migration 002: add_cgroup_fields_to_detached_processes
--
-- Adds nullable columns for cgroup v2 memory limit tracking.
-- Both columns default to NULL (no cgroup = no limit).

ALTER TABLE detached_processes ADD COLUMN memory_limit INTEGER;

ALTER TABLE detached_processes ADD COLUMN cgroup_path VARCHAR;
