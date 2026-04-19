# ADR-012: Diffable DB Tracking via sqlite-diffable

**Status**: Accepted
**Date**: 2026-04-18

## Context

The workspace git repo (see ADR-007 for the persistence layer and the `workspace-version-tracking` feature) commits workspace state on every session close. The workspace data lives in `.tachikoma/tachikoma.db`, a SQLite binary. The DB must be version-tracked so that pulling from a remote brings the latest state, but committing the binary directly causes repo bloat and opaque diffs (`Bin N -> M bytes`).

The core requirements are:

1. Git history should show *what changed* in the DB, not just "binary changed"
2. Pulling from a remote should restore the DB to the remote's state
3. No external system dependencies beyond what `uv sync` provides
4. Restore only when DB-related files actually changed in the pull

## Decision

Adopt **sqlite-diffable** (`simonw/sqlite-diffable`) for diffable DB tracking. Gitignore the binary; track the dump directory.

- The DB binary (`.tachikoma/*.db`) is listed in `.gitignore` — never committed
- Before the commit agent runs, `sqlite-diffable dump --all` dumps each table to `.tachikoma/db-dump/` as `.metadata.json` + `.ndjson` pairs with deterministic row ordering
- The dump directory (`.tachikoma/db-dump/`) is tracked by git — DB changes surface as dump-file diffs in `git status`
- On pull, if dump files changed, the DB is rebuilt via `sqlite-diffable load --replace`; if no dump files changed, no restore happens
- `sqlite-diffable` is a Python package added to `pyproject.toml` — `uv sync` installs the CLI console script, no system-level dependency

### Bootstrap order

The git bootstrap hook runs *before* the database hook so that DB restore from dump files completes before the database engine opens. The hook sequence is: `workspace → logging → git → database → ...`.

### Dump flow

`GitProcessor.process()` calls `dump_database()` before the dirty-check. This is critical — the DB binary is gitignored, so DB changes don't appear in `git status`. Only by dumping to tracked text files do those changes become visible to git. The dump directory is cleared before writing to prevent stale files from dropped tables accumulating.

### Restore flow

`smart_pull()` captures HEAD before pulling and returns the list of changed files alongside the result. `_sync_workspace()` checks if any changed file is under `.tachikoma/db-dump/` and only rebuilds the DB when dump files actually changed. Restore failure is non-fatal — logs a warning and lets `database_hook` create a fresh empty DB via `create_all()` + migrations.

## Consequences

### Positive

- Git history shows row-level diffs per table — actual DB changes are inspectable
- No system-level dependency — `sqlite-diffable` is a pip-installable Python package
- Repo stays small — only diffable text files are committed, not the binary
- Restore is targeted — DB rebuild only happens when dump files changed in the pull
- Deterministic output — unchanged tables produce identical files, so commits only touch tables that actually changed

### Negative

- Adds a Python dependency (`sqlite-diffable`) and a pre-commit dump step
- Fresh clones require the dump-to-DB restore step (runs automatically in the git bootstrap hook)
- Schema is not exported — managed by SQLAlchemy's `create_all()` + pragma migrations. sqlite-diffable handles data only. If the schema changes between dump and restore, the load may fail (non-fatal — fresh DB created)
- The dump step runs on every `GitProcessor.process()` call, even if the DB didn't change. The cost is minimal (sqlite-diffable reads within a transaction, produces identical files for unchanged tables)

## Alternatives Considered

### Committing the binary directly

- **Description**: Track `.tachikoma/tachikoma.db` in git as-is.
- **Why rejected**: Repo bloat — each commit stores a full copy of the ~10 MB binary. Diffs are opaque (`Bin N -> M bytes`). After several months the pack size grows by one DB-size per session.

### Git LFS for the binary

- **Description**: Use Git LFS to store the binary in a separate object store, keeping git history small via pointer files.
- **Why rejected**: Adds a system-level dependency (`git-lfs`). Commit diffs remain opaque (LFS pointer change only). Cloning requires `git-lfs` installed and `git lfs pull` to materialize the DB. No diffability benefit.

### Stdlib `iterdump()` SQL dump

- **Description**: Call `sqlite3.Connection.iterdump()` to produce a single `tachikoma.sql` file; gitignore the binary. Zero new deps.
- **Why rejected**: One monolithic SQL file that changes on every session close — noisy diffs and poor locality. No deterministic row ordering.

### Per-table CSV with `ORDER BY pk`

- **Description**: Hand-rolled exporter that emits one CSV per table sorted by primary key, plus a `_schema.sql`.
- **Why rejected**: Requires writing and maintaining a custom exporter with NULL/BLOB/JSON handling and a round-trip loader. `sqlite-diffable` already solves this with a well-tested CLI.

### Git `textconv` driver

- **Description**: Ship a `.gitattributes` entry `*.db diff=sqlite3` and a repo-local `.git/config` entry that runs `sqlite3 $1 .dump` on demand.
- **Why rejected**: Does not solve repo bloat — the binary is still committed as-is. `.git/config` isn't committed, so every clone needs manual setup.

### Dolt (git-native database)

- **Description**: Replace SQLite with Dolt; use its native per-row diffability and MySQL-wire protocol.
- **Why rejected**: Full replacement of the persistence stack — different dialect, mandatory `dolt sql-server` process, largely abandoned official Python client. Out of proportion for the problem.

---

## Notes

- `sqlite-diffable` is added to `pyproject.toml` dependencies. The CLI is available after `uv sync`.
- The dump runs before the dirty-check in `GitProcessor.process()` — this ordering is critical for DB changes to surface in `git status`.
- Restore is gated on dump-file changes in the pull result — avoids unnecessary DB rebuilds.
- Revisit if: the dump/restore round-trip becomes a performance bottleneck, or if schema-only changes need explicit tracking.
