# ADR-012: Diffable DB Tracking via text dumps

**Status**: Accepted
**Date**: 2026-04-18
**Revised**: 2026-04-19 — replaced the `sqlite-diffable` CLI dependency with an inline stdlib implementation that keeps the same file format.

## Context

The workspace git repo (see ADR-007 for the persistence layer and the `workspace-version-tracking` feature) commits workspace state on every session close. The workspace data lives in `.tachikoma/tachikoma.db`, a SQLite binary. The DB must be version-tracked so that pulling from a remote brings the latest state, but committing the binary directly causes repo bloat and opaque diffs (`Bin N -> M bytes`).

The core requirements are:

1. Git history should show *what changed* in the DB, not just "binary changed"
2. Pulling from a remote should restore the DB to the remote's state
3. No external system dependencies beyond what `uv sync` provides
4. Restore only when DB-related files actually changed in the pull

## Decision

Track a **diffable text dump** of the workspace DB in git; gitignore the binary. The dump/restore logic lives in `src/tachikoma/git/db_sync.py` and uses only the Python stdlib (`sqlite3` + `json`). The on-disk format is compatible with `simonw/sqlite-diffable` so previously-committed dumps restore without migration.

- The DB binary (`.tachikoma/*.db`) is listed in `.gitignore` — never committed
- Before the commit agent runs, `dump_database()` dumps each table to `.tachikoma/db-dump/` as `.metadata.json` + `.ndjson` pairs with deterministic row ordering
- The dump directory (`.tachikoma/db-dump/`) is tracked by git — DB changes surface as dump-file diffs in `git status`
- On pull, if dump files changed, the DB is rebuilt via `restore_database()`; if no dump files changed, no restore happens
- No third-party runtime dependency — the implementation is ~100 lines of stdlib code

### File format

Per table, two files in `.tachikoma/db-dump/`:

- `<table>.metadata.json`: `{"name": str, "columns": [str], "schema": str}` where `schema` is the `CREATE TABLE` statement from `sqlite_master`
- `<table>.ndjson`: one JSON array per row, values in column order. Non-JSON-serializable values (e.g. raw bytes) fall back to `repr()`.

`sqlite_sequence` (SQLite's internal AUTOINCREMENT bookkeeping) is excluded from both dump and restore — SQLite rebuilds it automatically from `MAX(rowid)` on the next insert into an AUTOINCREMENT table.

### Bootstrap order

The git bootstrap hook runs *before* the database hook so that DB restore from dump files completes before the database engine opens. The hook sequence is: `workspace → logging → git → database → ...`.

### Dump flow

`GitProcessor.process()` calls `dump_database()` before the dirty-check. This is critical — the DB binary is gitignored, so DB changes don't appear in `git status`. Only by dumping to tracked text files do those changes become visible to git. The dump directory is cleared before writing to prevent stale files from dropped tables accumulating.

### Restore flow

Two complementary restore paths ensure the DB is rebuilt from dumps when needed:

1. **Sync-triggered restore** (git hook): `smart_pull()` captures HEAD before pulling and returns the list of changed files alongside the result. `_sync_workspace()` checks if any changed file is under `.tachikoma/db-dump/` and only rebuilds the DB when dump files actually changed during the pull.
2. **Missing-DB restore** (database hook): On startup, if the DB file is missing but dump files exist (e.g., fresh clone, deleted DB), `database_hook` calls `restore_database()` before initializing the engine. This covers cases where the git hook's sync didn't trigger a restore (dumps were already present, no pull needed) but the DB binary is absent.

Both restore paths are non-fatal — logs a warning on failure and lets `database_hook` create a fresh empty DB via `create_all()` + migrations.

## Consequences

### Positive

- Git history shows row-level diffs per table — actual DB changes are inspectable
- Zero runtime dependencies — the implementation uses only the stdlib
- No subprocess / binary-resolution quirks (previously we had to resolve the CLI script from the `uv tool` venv's `bin/` dir)
- Errors surface as native Python exceptions instead of being smuggled through a subprocess `stderr` pipe
- Repo stays small — only diffable text files are committed, not the binary
- Restore is targeted — DB rebuild only happens when dump files changed in the pull
- Deterministic output — unchanged tables produce identical files, so commits only touch tables that actually changed
- Backwards-compatible with existing `simonw/sqlite-diffable` dumps already committed to workspace repos

### Negative

- Fresh clones require the dump-to-DB restore step (runs automatically in the git bootstrap hook)
- Schema is not exported beyond the `CREATE TABLE` statement — SQLAlchemy's `create_all()` + pragma migrations remain the source of truth at runtime. If the schema changes between dump and restore, the load may fail (non-fatal — fresh DB created)
- The dump step runs on every `GitProcessor.process()` call, even if the DB didn't change. The cost is minimal (single `SELECT` per table, identical output for unchanged tables)

## Alternatives Considered

### Shelling out to the `sqlite-diffable` CLI

- **Description**: Use `simonw/sqlite-diffable` as a `pyproject.toml` dep and invoke its console script via `asyncio.create_subprocess_exec`.
- **Why rejected** (after initial adoption): The CLI logic is ~30 lines on top of `sqlite_utils`. Shelling out added a third-party dep (plus `sqlite_utils` transitively), a binary-resolution hack for `uv tool install` installs, subprocess error plumbing, and no real value over a direct stdlib implementation. We retained the on-disk format for backwards compatibility.

### Committing the binary directly

- **Description**: Track `.tachikoma/tachikoma.db` in git as-is.
- **Why rejected**: Repo bloat — each commit stores a full copy of the ~10 MB binary. Diffs are opaque (`Bin N -> M bytes`). After several months the pack size grows by one DB-size per session.

### Git LFS for the binary

- **Description**: Use Git LFS to store the binary in a separate object store, keeping git history small via pointer files.
- **Why rejected**: Adds a system-level dependency (`git-lfs`). Commit diffs remain opaque (LFS pointer change only). Cloning requires `git-lfs` installed and `git lfs pull` to materialize the DB. No diffability benefit.

### Stdlib `iterdump()` SQL dump

- **Description**: Call `sqlite3.Connection.iterdump()` to produce a single `tachikoma.sql` file; gitignore the binary. Zero new deps.
- **Why rejected**: One monolithic SQL file that changes on every session close — noisy diffs and poor locality. No deterministic row ordering.

### Git `textconv` driver

- **Description**: Ship a `.gitattributes` entry `*.db diff=sqlite3` and a repo-local `.git/config` entry that runs `sqlite3 $1 .dump` on demand.
- **Why rejected**: Does not solve repo bloat — the binary is still committed as-is. `.git/config` isn't committed, so every clone needs manual setup.

### Dolt (git-native database)

- **Description**: Replace SQLite with Dolt; use its native per-row diffability and MySQL-wire protocol.
- **Why rejected**: Full replacement of the persistence stack — different dialect, mandatory `dolt sql-server` process, largely abandoned official Python client. Out of proportion for the problem.

---

## Notes

- Dump/restore logic lives in `src/tachikoma/git/db_sync.py` — no external dependency.
- The dump runs before the dirty-check in `GitProcessor.process()` — this ordering is critical for DB changes to surface in `git status`.
- Restore is gated on dump-file changes in the pull result — avoids unnecessary DB rebuilds.
- Revisit if: the dump/restore round-trip becomes a performance bottleneck, or if schema-only changes need explicit tracking.
