# DES-006: State-Based, Marker-Free Migration Detection

**Scope**: Project-wide
**Date**: 2026-06-17
**Last Updated**: 2026-06-17

## Pattern

A one-time workspace adaptation — a data migration, a store reshape, a format conversion — detects remaining work by inspecting the **presence of the pre-migration state itself** (rows in a table, files in a directory, non-empty content where the new shape is expected to be empty), and runs **idempotently** so a re-run after an interruption converges rather than duplicates. It writes **no persisted completion marker**: the resulting filesystem/database state *is* the "done" signal, reached only when the adapting step fully completes. A run that finds no remaining pre-migration state returns immediately (a fast no-op), so the same hook is safe on fresh installs and already-migrated workspaces.

The critical correctness property: detect on the **old-state** presence, not the **new-state** presence. An interrupted pass that has written some new state but not finished removing the old must still read as "work remaining" so it re-runs cleanly; keying on new-state presence would falsely report "done" the moment any new state exists.

Runs that mutate the workspace commit the durable result **before** destroying the source (fold-before-empty), so the pre-migration state is always git-recoverable; an agent-driven fold is written as merge/dedup so a partial re-run updates rather than appends.

## Rationale

A persisted completion flag (a DB key, a file sentinel) is a second source of truth that can drift from the actual workspace state — set before the work fully landed, or left unset after a manual fix. State-based detection has one source of truth (the workspace itself), needs no migration-record schema, and is intrinsically safe to re-run: there is no flag to get wrong. This is the same posture the codebase takes for crash recovery elsewhere — derive what to do from observable state rather than a tracked boolean — and it makes an interrupted pass self-healing: the leftover old state drives the retry.

## Examples

### Do This

```
// detect: is there pre-migration work left? (OLD-state content presence)
if countNonEmpty(files("legacy-store/")) == 0:
    return                          // no-op — fresh install or already migrated

// adapt idempotently — merge/dedup, so a re-run never duplicates
await agent.fold("legacy-store/", "new-store/")   // updates existing new-state, never appends

// commit the durable result BEFORE destroying the source
await commit("fold legacy store into new store")

await sweep("legacy-store/")        // now-empty source files removed; empty store == done
await commit("sweep emptied legacy store")
```

**Why**: Detection keys on old-state content presence, so an interruption between the fold and the sweep still leaves old content and the next run re-runs and re-merges. The fold commits before the sweep, so the pre-migration state is recoverable. No marker is written — an empty legacy store is unambiguously "done."

### Don't Do This

```
// detect on the NEW state's presence, and gate with a persisted flag
if exists("new-store/") or db.get("migration.done"):
    return
await agent.fold("legacy-store/", "new-store/")
db.set("migration.done", true)
await sweep("legacy-store/")
```

**Why**: Two failure modes. (1) Keying on `new-store/` presence false-reports "done" after a crash that created `new-store/` but never swept the legacy store — the legacy content is stranded. (2) The `migration.done` flag is a second source of truth: a crash between the fold and `db.set`, or a flag set with a failed sweep, lets the flag and the real state diverge so the pass never re-runs.

## Exceptions

- A migration whose pre-migration state is not observable from the workspace (an in-memory-only or remote shape) cannot detect by state presence and may need an explicit record — but prefer making the state observable first.
- Migrations that cannot be made idempotent (a non-mergeable side effect with no dedup key) need a different strategy; this pattern assumes the adapting step is merge/dedup or already-done-checkable.

---

## Related

- See also: [DES-003](DES-003-testing-conventions.md) — migrations are tested against real temp workspaces with the adapting step faked, asserting both detection and re-run idempotency
- Related feature: [../feature-designs/migration.md](../feature-designs/migration.md) — the legacy-install workspace migration (DB/filesystem state detection)
- Related feature: [../feature-designs/memory.md](../feature-designs/memory.md) — the memory-store migration (legacy-content detection, fold-before-empty, two commits bracketing the sweep)
