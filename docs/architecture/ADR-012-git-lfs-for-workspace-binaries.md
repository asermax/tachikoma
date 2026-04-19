# ADR-012: Git LFS for Workspace Binary Artifacts

**Status**: Accepted
**Date**: 2026-04-18
**Last Updated**: 2026-04-18

## Context

The workspace git repo (see ADR-007 for the persistence layer and the `workspace-version-tracking` feature) commits `.tachikoma/tachikoma.db` on every session close. The DB is a ~10 MB SQLite binary, and after several months of use the pack size on `~/tachikoma/.git` grew past 290 MB — each commit stores a full fresh copy of the binary, so the repo bloats roughly one DB-size per session. The commit diffs are `Bin N -> M bytes`, i.e. opaque.

DLT-121 framed the problem as "make DB changes diffable". During speccing we evaluated diffable-text alternatives (sqlite-diffable sidecar, stdlib `iterdump()` SQL dump, per-table CSV with PK ordering, git textconv driver) and decided the tradeoff wasn't worth it for this project:

- Textual DB diffs are occasionally interesting (for the agent inspecting its own history) but rarely load-bearing — the memory files in `memories/` and `context/` already carry the narrative, and tables like `sessions`, `task_instances`, and `workflow_states` are append-mostly machine state.
- Any diffable format requires either a new dump step on every commit, a new dep, a bespoke exporter, or a mandatory per-clone git config.
- The real pain is repo size, not opacity.

Git LFS addresses the size problem directly: the binary moves to a separate object store, git history only references it via a small pointer file, and the commit/push flow remains untouched at the call-site.

## Decision

Adopt **Git LFS** for `.tachikoma/*.db` in workspace repos. Do not pursue diffable-text dumps.

- Fresh workspaces bootstrap with LFS enabled: `git_hook` runs `git lfs install --local` and writes a `.gitattributes` line `.tachikoma/*.db filter=lfs diff=lfs merge=lfs -text` before any DB is committed.
- The bootstrap hook requires `git-lfs` to be installed on the host and fails fast with an install hint when it isn't.
- Existing workspaces that predate this decision are migrated one-time via `git lfs migrate import --include=".tachikoma/*.db" --everything`; the hook logs a warning on startup until the migration runs.
- Runtime behavior (the Haiku commit agent, `GitProcessor`, `smart_push`, `smart_pull`) is unchanged. LFS clean/smudge filters run inside `git add`/`git push` automatically.

## Consequences

### Positive

- Git pack stays small regardless of DB-commit frequency — one DB-sized pointer delta per commit instead of a full binary copy.
- Runtime code path is unchanged: the commit agent prompt doesn't mention LFS, `smart_push` doesn't mention LFS, SQLAlchemy + aiosqlite continue unaffected.
- Historical DB snapshots remain accessible (unlike `filter-repo --invert-paths`) — `git lfs checkout <ref>` can recover any past DB state.
- Scoped `.gitattributes` pattern (`.tachikoma/*.db`, not `*.db`) means user-authored `.db` files elsewhere in the workspace aren't dragged into LFS unexpectedly.

### Negative

- Adds a system-level dependency on `git-lfs` — not installed by default on most Linux distros. Bootstrap fails fast with install guidance (e.g. `pacman -S git-lfs`).
- Commit diffs for the DB stay opaque (LFS pointer change only). This is the explicit tradeoff: diffability was dropped for operational simplicity.
- One-time migration required for pre-existing workspaces — rewrites history and requires force-push if the workspace has an origin remote.
- Cloning a workspace repo to another machine requires both `git-lfs` installed and `git lfs pull` to materialize the DB — the bare `git clone` leaves a pointer file in place of the binary.

## Alternatives Considered

### sqlite-diffable sidecar

- **Description**: Use `simonw/sqlite-diffable` to dump each table to a pair of `metadata.json` + `ndjson` files alongside (or instead of) the binary. Commit the dump dir; gitignore the binary. Round-trippable via `sqlite-diffable load --replace`.
- **Why rejected**: Adds a dep and a pre-commit dump step; diffs would be per-table and localized, but the operational cost (a new toolchain to reason about, a rehydrate step on fresh clones) exceeded the value given that the DB content is rarely reviewed by humans.

### Stdlib `iterdump()` SQL sidecar

- **Description**: Call `sqlite3.Connection.iterdump()` from a pre-commit hook to produce a single `tachikoma.sql` file; gitignore the binary. Zero new deps.
- **Why rejected**: Same class of tradeoff as sqlite-diffable but with a worse diff story — one monolithic SQL file touches on every session close, making noisy diffs and large blobs inside git (even if compressible).

### Per-table CSV with `ORDER BY pk`

- **Description**: Hand-rolled exporter that emits one CSV per table sorted by primary key, plus a `_schema.sql`. Cleanest row-level diffs because PK-sorted output survives rowid churn.
- **Why rejected**: Requires writing and maintaining a custom exporter with NULL/BLOB/JSON handling and a round-trip loader. The marginal diff-quality win over `sqlite-diffable` isn't worth the code to own.

### Git `textconv` driver only

- **Description**: Ship a `.gitattributes` entry `*.db diff=sqlite3` and a repo-local `.git/config` entry that runs `sqlite3 $1 .dump` on demand. `git diff`/`git log -p` renders binary as text on read.
- **Why rejected**: Does not solve repo bloat — the binary is still committed as-is. `.git/config` isn't committed either, so every clone needs manual setup.

### Git LFS with `filter-repo --invert-paths` instead of `migrate import`

- **Description**: Use `git filter-repo` to drop all historical `.db` blobs from the repo entirely before switching to LFS for future commits. Maximum space savings.
- **Why rejected**: Loses the ability to recover historical DB states. `migrate import` is only marginally larger and keeps the historical snapshots retrievable via LFS.

### Dolt (git-native database)

- **Description**: Replace SQLite with Dolt; use its native per-row diffability and MySQL-wire protocol.
- **Why rejected**: Full replacement of the persistence stack — different dialect (`asyncmy` or `aiomysql` instead of `aiosqlite`), mandatory `dolt sql-server` process alongside the app, and a largely abandoned official Python client. Out of proportion for the problem.

---

## Notes

- System prereq: `git-lfs` ≥ 3.x. Arch: `pacman -S git-lfs`. Debian/Ubuntu: `apt install git-lfs`. macOS: `brew install git-lfs`.
- One-time migration command for existing workspaces: `git lfs migrate import --include=".tachikoma/*.db" --everything`, followed by `git reflog expire --expire=now --all && git gc --aggressive --prune=now` to reclaim the old pack space.
- Revisit if: the agent gains a workflow that relies on reading historical DB rows (at which point a diffable sidecar might become worth the operational cost), or if LFS storage cost on a paid remote becomes a concern.
