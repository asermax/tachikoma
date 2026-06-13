# Design: Migration

<!-- This design describes the current implementation approach. Updated through delta reconciliation. -->

**Feature Spec**: [../feature-specs/migration.md](../feature-specs/migration.md)
**Status**: Current

## Purpose

Explains how the migration subsystem adapts a workspace last used by a legacy install into the current layout: the two orchestration phases woven around database creation, the detect-then-act shape every step shares, and the non-destructive backup strategy that lets the subsystem run unconditionally at every startup.

## Problem Context

A workspace carried over from a legacy install has a database under a different schema, a config in the old shape, identity files under a `context/` subdirectory, skill frontmatter with keys the current loader does not understand, and scheduled task definitions worth preserving. Startup must make such a workspace usable without manual intervention, but must equally leave a fresh or already-adapted workspace alone — the same code path runs every boot.

**Constraints:**
- The legacy database file must be moved aside *before* drizzle opens it, or drizzle would try to apply migrations onto an incompatible schema; this step therefore cannot be best-effort — its failures must propagate
- Task definitions can only be imported *after* the live database exists and its migrations have run, so the import is a separate phase reading from the backup the database step left behind
- Adaptation must be safe to run on every startup, including unattended/non-interactive ones, so detection must be cheap and prompts must have a safe default
- Nothing may be deleted: the user must be able to recover their old data, so legacy files are renamed or copied to a `.legacy-backup` rather than removed

**Interactions:**
- Startup wiring (`src/app.ts`) calls `adaptConfig` (before constructing the workspace-dependent services), `adaptWorkspace` (before `createDatabase`), and `adaptWorkspaceData` (after `runMigrations`); the translated config is merged back into the shared `config` object in place
- Workspace layout (`databaseFile`, `dataDir`) comes from the core shell (see [core-shell](./core-shell.md))
- Imported definitions land in the `task_definitions` table owned by the tasks extension and are picked up by its scheduler (see [tasks](./tasks.md))
- Context files relocate to where foundational context reads them (see [foundational-context](./foundational-context.md)); episodic memory files are the durable record of conversations not imported from the legacy database (see [memory](./memory.md))

## Design Overview

The subsystem is a set of independent, self-detecting steps behind two orchestrators in `src/migration/index.ts`. `adaptWorkspace` runs the file-level steps before the database is opened; `adaptWorkspaceData` runs the database-dependent task import after it. The database step is awaited directly so its failures abort startup; every other step is wrapped in a try/catch that logs a warning and continues, because a partially-adapted workspace is still bootable and a failed cosmetic step should not block the user.

Every step shares the same shape: probe the workspace for a legacy marker, return immediately if absent, otherwise back up and transform. Detection is by content, not by a stored "migrated" flag — markers are the legacy database's schema tables, the old config's table shape, the presence of `context/` files, and legacy-only frontmatter keys — so re-running after a successful adaptation naturally finds nothing. The one exception is the task import, which has no surviving on-disk marker once definitions are in the live database, so it is additionally guarded by a one-time flag in the migration key-value state.

```
app.ts startup
  ├─ adaptConfig(configPath)              detect old-shape → backup + translate → reload
  │     (translated Config merged into the shared config in place)
  ├─ adaptWorkspace(workspace)
  │     ├─ adaptLegacyDatabase   AWAITED — rename legacy db (+ wal/shm) → backup   [must precede drizzle]
  │     ├─ adaptContextFiles     best-effort — move context/*.md → workspace root
  │     └─ adaptSkillsFrontmatter best-effort — offer to strip depends_on/version
  ├─ createDatabase + runMigrations       drizzle opens a fresh db
  └─ adaptWorkspaceData(db, workspace)
        └─ adaptLegacyTasks      best-effort — import task_definitions from the backup db (once)
```

## Components

### Implementation Structure

| Component | Responsibility | Key Decisions |
|-----------|----------------|---------------|
| `src/migration/index.ts` | The two orchestrators (`adaptWorkspace`, `adaptWorkspaceData`) | Database step awaited (failures propagate); every other step try/caught and logged so startup continues |
| `src/migration/database.ts` | `adaptLegacyDatabase`: detect legacy markers, rename db + sidecars to the backup | Markers are `alembic_version`/`schema_migrations`; rename (never delete) including `-wal`/`-shm`; refuse to overwrite an existing backup; runs before drizzle opens the file |
| `src/migration/config.ts` | `adaptConfig`/`translateOldConfig`: detect old-shape config, back it up, translate, reload | Old-shape detection by `[telegram]`/string `channel`/old `[agent]` keys; role models confirmed interactively and provider-qualified; unmapped sections dropped with a log; original preserved at `<path>.legacy-backup` |
| `src/migration/context.ts` | `adaptContextFiles`: move `SOUL.md`/`USER.md`/`AGENTS.md` from `context/` to the root | Skip-on-conflict (root file wins); `rmdir` tidies `context/` only when empty |
| `src/migration/skills.ts` | `adaptSkillsFrontmatter`/`stripLegacyFrontmatter`: offer to strip `depends_on`/`version` | Pure string transform that drops a key and its continuation lines; declining is harmless (loader ignores the keys) |
| `src/migration/tasks.ts` | `adaptLegacyTasks`: import legacy `task_definitions` into the live db | Reads the readonly backup db directly; one-time flag + `onConflictDoNothing`; preserves id/`since`/`created_at` for cron anchoring; drops instances and skill pins |
| `src/migration/ask.ts` | `createAsk`: the shared yes/no prompt | TTY-only; non-interactive answers "no"; prompts on stderr so REPL stdout stays clean |
| `src/migration/fs.ts` | `pathExists` | A stat-based existence check shared by every step |

## Key Decisions

### Rename and back up, never delete

**Choice**: The legacy database is renamed to `tachikoma.legacy-backup.db` (with its `-wal`/`-shm` sidecars), and the legacy config is copied to `<path>.legacy-backup` before being rewritten. An existing backup is never overwritten — if one is already present, the step backs off and leaves both files untouched.
**Why**: Adaptation is irreversible from the user's point of view, and a transform that loses data the user did not explicitly discard is unacceptable. A rename is atomic and keeps everything recoverable; backing off on an existing backup avoids clobbering a prior (possibly hand-curated) recovery copy.
**Consequences**:
- Pro: Any legacy data not carried forward — transcripts, instances, the dropped tables — stays readable in the backup
- Pro: Re-running is safe; a second legacy database (unusual) is detected and left alone rather than overwriting the backup
- Con: The backup files accumulate in `dataDir` and are never cleaned up by the system

### Detect by content, not a stored flag

**Choice**: Each file-level step decides whether to act by probing the workspace for a legacy marker (schema tables, old config shape, `context/` files, legacy frontmatter keys) rather than by recording that migration has run.
**Why**: A content marker is self-clearing — once the database is renamed, the config translated, and the files moved, the markers are gone, so the next startup is a natural no-op without any bookkeeping to keep in sync. It also means a workspace that was only partially adapted (e.g. config translated but a later step failed) is correctly re-attempted on the next boot.
**Consequences**:
- Pro: No "migration version" state to maintain or risk going stale
- Pro: Partial adaptations self-heal across restarts
- Con: Detection runs on every startup; mitigated by it being a few `stat`/probe calls

### One-time flag for the task import

**Choice**: `adaptLegacyTasks` is guarded by a `legacy-task-definitions` boolean in the migration key-value state, set after the first import, in addition to inserting with `onConflictDoNothing` on the definition id.
**Why**: Unlike the file-level steps, the task import leaves no on-disk marker that clears itself — the backup database stays present (by design), so a content-only check would re-import on every boot. That would resurrect definitions the user deleted after the first import. The flag makes the import a true one-shot; `onConflictDoNothing` is the second layer of idempotency, covering a re-run within the same import (and any id that already exists for another reason).
**Consequences**:
- Pro: Deleted definitions stay deleted; the import runs exactly once
- Pro: Idempotent twice over — the flag stops repeat passes, conflict-skip stops duplicate ids within a pass
- Con: Clearing the flag (manual state edit) is the only way to re-trigger an import

### Interactive with a safe non-interactive default

**Choice**: Steps that mutate user-editable content (config role models, skill frontmatter) ask via the shared `Ask` primitive, which prompts only on a TTY and otherwise answers "no" with a warning.
**Why**: Provider-qualifying a model alias or stripping frontmatter is a judgment call the user should make, but adaptation must also survive an unattended boot. Defaulting to "no" means the worst case of a non-interactive startup is an unset role or retained (ignored) frontmatter keys — never a wrong guess written to the user's files.
**Consequences**:
- Pro: Unattended startups never block on a prompt and never make a silent destructive choice
- Con: A non-interactive first boot leaves role models unset and frontmatter unstripped, requiring a later interactive run or manual edit

### Phase split around database creation

**Choice**: The database rename runs (awaited, failures propagating) before `createDatabase`; the task import runs (best-effort) after `runMigrations`.
**Why**: drizzle must open a fresh file, so the legacy database has to be gone first, and a failure there must abort rather than let drizzle corrupt or misread an incompatible file. The import, conversely, needs the live schema to exist before it can insert, and reads the definitions out of the very backup the first step produced — so it can only run on the far side of database creation.
**Consequences**:
- Pro: drizzle never sees a legacy schema; the import always has a live target and a readable source
- Con: The two halves are separated in `app.ts` by the database-creation code, so the migration flow is not in one place

## System Behavior

### Scenario: fresh install, nothing to adapt

**Given**: A brand-new workspace with no legacy database, a new-shape (or absent) config, no `context/` directory, and no legacy frontmatter
**When**: Startup runs the migration steps
**Then**: `adaptConfig` returns null (not old-shape), `adaptLegacyDatabase` finds no database file, `adaptContextFiles` finds nothing under `context/`, `adaptSkillsFrontmatter` finds no legacy keys, and `adaptLegacyTasks` finds no backup database — every step is a no-op and startup proceeds normally.

### Scenario: legacy workspace adapted on first boot

**Given**: A workspace carried over from a legacy install — an `alembic_version`-bearing database, an old-shape config with `[telegram]` and a bare `model = "opus"`, `context/SOUL.md`, and a skill with `depends_on` in its frontmatter, on an interactive terminal
**When**: Startup runs
**Then**: the config is backed up and translated (the user confirms `anthropic/opus` for the main role; unmapped sections are logged as dropped); the database and its sidecars are renamed to `tachikoma.legacy-backup.db`; drizzle creates a fresh database and applies migrations; `SOUL.md` moves to the workspace root and the empty `context/` is removed; the user confirms stripping `depends_on`; and the legacy `task_definitions` are imported (ids, schedules, and `since`/`created_at` preserved) with the one-time flag then set. Conversation history, instances, and the other legacy tables remain only in the backup.

### Scenario: already-adapted workspace re-run

**Given**: The workspace from the previous scenario, restarted
**When**: Startup runs the migration steps again
**Then**: the config is now new-shape (null), the live database carries no legacy markers (the legacy one is already the backup, and the step refuses to touch it again because a backup exists), `context/` is gone, no skill carries a legacy key, and the `legacy-task-definitions` flag is set — so the task import returns immediately without re-reading the still-present backup. Nothing changes; any definitions the user deleted in the meantime stay deleted.

## Notes

- The backup files (`tachikoma.legacy-backup.db` and its sidecars, `<config>.legacy-backup`) live in `dataDir` / next to the config and are never cleaned up automatically — they are the user's recovery copy.
- `adaptLegacyTasks` opens the backup with `better-sqlite3` directly in readonly mode rather than through drizzle, because the backup is a foreign-schema file and only a couple of columns are read from it.
- Legacy schedule blobs are tolerant-parsed: a JSON discriminated union, a JSON string instant, or (in the oldest installs) a bare ISO instant all map to the stored `cron`/`once` union; anything unparseable skips the one definition with a warning rather than failing the import.
