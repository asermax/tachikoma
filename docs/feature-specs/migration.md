# Migration

<!-- This spec describes the current system capability. Updated through delta reconciliation. -->

## Overview

The `migration` subsystem (`src/migration/`) adapts a workspace last used by a legacy install into the layout the current system expects. It runs unconditionally at startup, before any channel comes up: every step first detects whether there is anything to adapt and is a fast no-op on a pristine or already-adapted workspace, so the cost of running it on a clean install is a handful of `stat` calls.

Adaptation spans four concerns — the database file, the config file, the context files, and skill frontmatter — plus a separate database-dependent step that imports legacy task definitions after the live database has been created and migrated. The guiding principle is non-destructive: legacy data is renamed to a backup or preserved in place, never deleted, and durable user intent (config values, identity files, task schedules) is carried forward while transient or unresumable state is left behind in the backup.

Steps that touch user-editable content (config role models, skill frontmatter) ask before mutating, and default to the safe answer when startup is non-interactive.

## User Stories

- As a user upgrading from a legacy install, I want my existing workspace to keep working without manual surgery so that I do not have to hand-edit config, move files, or re-create my scheduled tasks
- As a user, I want nothing destroyed during adaptation so that I can recover my old data from a backup if something goes wrong
- As a user on a fresh or already-upgraded install, I want startup to be unaffected so that the migration logic is invisible once there is nothing to adapt

## Requirements

| ID | Requirement |
|----|-------------|
| R0 | Every step is self-detecting: it inspects the workspace for legacy markers and returns a no-op when none are present, so a pristine or already-adapted workspace incurs only detection cost |
| R1 | The database step (`adaptLegacyDatabase`) runs first, before drizzle opens the database file; its failures propagate and abort startup. Every other step is best-effort: a failure is logged and startup continues |
| R2 | Legacy task import (`adaptLegacyTasks`) runs after the live database is created and migrations are applied, reading from the backup the database step left behind |
| R3 | Adaptation is idempotent: re-running on a workspace that was already adapted detects no legacy markers (or finds the one-time import flag already set) and changes nothing |
| R4 | No legacy data is deleted. The legacy database is renamed to `tachikoma.legacy-backup.db`; the legacy config is copied to `<path>.legacy-backup` before being rewritten; context files are moved (not copied away) only when no destination conflict exists |
| R5 | The legacy database is detected by the presence of an `alembic_version` or `schema_migrations` table; a database without either marker is left untouched |
| R6 | When renaming the legacy database, its `-wal` and `-shm` sidecar journals are moved alongside the main file so the backup stays recoverable; an existing backup is never overwritten |
| R7 | Legacy config is detected by old-shape markers (`[telegram]` table, a string `channel`, or old `[agent]` role keys) and translated into the new layout; the original is preserved in the backup and the reloaded `Config` is returned for the rest of startup |
| R8 | Config role models are translated interactively: each legacy bare alias (e.g. `opus`) is offered as a provider-qualified id (`anthropic/opus`) and only set if confirmed; declined roles are left unset with a warning |
| R9 | Config sections without a new-layout equivalent are dropped with an info log naming them; the only implicit carry-over is `tasks.timezone → scheduler.timezone` |
| R10 | Legacy context files (`SOUL.md`, `USER.md`, `AGENTS.md`) under `context/` are moved to the workspace root; a file that already exists at the root is left in `context/` with a warning, and the `context/` directory is removed only once empty |
| R11 | Skill frontmatter carrying legacy-only keys (`depends_on`, `version`) is offered for stripping interactively; declining keeps the files unchanged (the loader ignores the keys regardless) |
| R12 | Task definitions are imported preserving their legacy id, `schedule` (cron or one-shot), `since`, and `created_at` so cron anchoring and stale-cron prevention carry over; the import is guarded by a one-time `legacy-task-definitions` flag and inserts skip ids that already exist |
| R13 | A definition with an unrecognized schedule or task type is skipped with a warning; the import continues with the rest |
| R14 | Interactive prompts run only on a TTY; a non-interactive startup answers every prompt "no" with a warning, so adaptation never blocks an unattended boot |

## What Is Deliberately Not Carried Over

Adaptation imports durable user intent and leaves everything else behind in the renamed backup, which is never deleted. The following legacy data is **not** migrated, by design:

- **Conversation history / transcripts** — the legacy transcript format is not resumable as a pi session, so re-importing it would produce dead rows. The durable content of past conversations already lives in the episodic memory files (see [memory](memory.md)), which travel with the workspace untouched.
- **Task instances** — instances are firings generated from definitions; they are transient by nature. Only the definitions (the recurring intent) are imported, and the scheduler regenerates instances from them.
- **Skill pins on task definitions** — skill pinning no longer exists, so the legacy `skills` column is dropped during import.
- **`sessions`, `session_context_entries`, `session_resumptions`, `channel_messages`, `workflow_states`, `detached_processes`, `app_state` rows** — these are transient runtime bookkeeping or implementation-specific state with no meaning under the current schema. Sessions and resumptions describe unresumable transcripts; channel messages and detached-process records are point-in-time runtime state; workflow and app-state rows are tied to the previous implementation's internals.

All of this remains readable in `tachikoma.legacy-backup.db` for manual recovery.

## Behaviors

### Startup ordering (R0, R1, R2)

Migration is woven into the startup sequence at two points around database creation: the file-level adaptations run before the database is opened, and the task import runs after it has been created and migrated.

**Acceptance Criteria**:
- Given startup, when migration runs, then `adaptLegacyDatabase` completes before `createDatabase`/`runMigrations`, and `adaptLegacyTasks` runs only after migrations have applied
- Given the database step throws, then startup aborts (the failure propagates rather than being swallowed)
- Given any non-database step throws, then the error is logged as a warning and startup continues

### Database adaptation (R4, R5, R6)

The legacy database is detected by schema markers and renamed out of the way so drizzle starts from a fresh file.

**Acceptance Criteria**:
- Given a database file containing `alembic_version` or `schema_migrations`, when the step runs, then the file and any `-wal`/`-shm` sidecars are renamed to `tachikoma.legacy-backup.db` (and `.db-wal`/`.db-shm`) and a warning is logged
- Given a database file with neither marker, when the step runs, then it is left untouched
- Given a backup already exists at the target path, when a legacy database is detected, then both files are left untouched with a warning (no overwrite)
- Given the file cannot be probed, when the step runs, then it is left untouched with a warning

### Config translation (R7, R8, R9)

A legacy config is translated into the new shape, the original preserved, and the reloaded config handed back to startup.

**Acceptance Criteria**:
- Given a config with a `[telegram]` table, a string `channel`, or old `[agent]` role keys, when the step runs, then it is recognized as legacy and translated; a new-shape config returns null (no change)
- Given translation runs, then the raw original is written to `<path>.legacy-backup` before the translated file (with a header pointing at the backup) overwrites the original, and the reloaded `Config` is returned
- Given a legacy `[agent]` role with a bare alias `opus`, when the user is asked, then a "yes" sets the role to `anthropic/opus` and a "no" leaves it unset with a warning; an alias already containing `/` is offered verbatim
- Given config sections with no new-layout equivalent, when translation runs, then they are dropped with an info log naming them (except `tasks.timezone`, mapped to `scheduler.timezone`)

### Context file relocation (R10)

Identity files move from the legacy `context/` directory to the workspace root, where the current system reads them.

**Acceptance Criteria**:
- Given `context/SOUL.md` (or `USER.md`/`AGENTS.md`) exists and no same-named file is at the root, when the step runs, then the file is moved to the root
- Given the file exists in both `context/` and the root, when the step runs, then the root file is kept, the old copy is left in `context/`, and a warning is logged
- Given the `context/` directory is empty after the moves, then it is removed; a non-empty directory is left in place

### Skill frontmatter (R11)

Skill `SKILL.md` files carrying frontmatter keys only the legacy registry consumed are offered for cleanup.

**Acceptance Criteria**:
- Given one or more skills with `depends_on` or `version` in their frontmatter, when the user confirms, then those keys (and their continuation lines) are removed from each affected file
- Given the user declines (or startup is non-interactive), then the files are left unchanged with a warning — the loader ignores the keys
- Given no skill carries a legacy-only key, then the step is a no-op

### Task definition import (R2, R12, R13)

Definitions are ported from the backed-up legacy database into the live one, once.

**Acceptance Criteria**:
- Given the one-time `legacy-task-definitions` flag is not set and a legacy backup database exists, when the step runs, then each `task_definitions` row is inserted preserving its id, schedule, `since`, and `created_at`, and the flag is then set
- Given the flag is already set, when the step runs, then nothing is imported (so definitions the user later deletes do not reappear)
- Given a definition whose id already exists in the live database, when imported, then the insert is skipped (`onConflictDoNothing`)
- Given a definition with an unrecognized schedule or task type, when imported, then it is skipped with a warning and the rest still import
- Given the backup database is absent or unreadable, when the step runs, then it is a no-op (absent) or skipped with a warning (unreadable)

### Interactive prompting (R14)

All confirmations share one prompt primitive with a safe non-interactive default.

**Acceptance Criteria**:
- Given a TTY, when a step asks a yes/no question, then the prompt is shown on stderr and the answer parsed (`y`/`yes` → yes)
- Given a non-interactive startup (no TTY), when any step asks, then the answer is "no" with a warning, so adaptation never blocks
