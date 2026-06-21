# DES-007: Marker-Computed Effective State over an Append-Only Log

**Scope**: Project-wide
**Date**: 2026-06-21
**Last Updated**: 2026-06-21

## Pattern

When conversational state must *change* on the pi session tree — which is **append-only** and has no update or delete primitive ([ADR-014](../architecture/ADR-014-session-source-of-truth.md)) — do not try to mutate or remove an existing entry. Instead **append a marker** custom entry naming the target id, and compute the **effective** state at read time by scanning the markers. The immutable entry stays on the file untouched; its observable state is whatever the latest marker set says it is.

Concretely: `appendState(session, MARKER_TYPE, { targetId })` writes the marker; a scan builds a `Set<string>` of marked target ids once per read pass; an `effective<State>(targetId, markedSet)` helper resolves the effective value. Because the marker list only grows (append-only), "marked" is monotonic and restart-safe — the markers reload natively with the session file.

Two flavors recur:

- **Idempotency / completion markers** — "this unit of work is done" (a branch extracted, a pipeline step run). A recovery pass scans the markers to skip work that already completed.
- **Orphan / reclassification markers** — "treat this immutable entry as X now" (a summary a decision reversal orphaned). The entry's persisted `kind` is unchanged; its *effective* kind is computed from the marker so every consumer sees the new state through one chokepoint.

The critical correctness property: the marker is the **only** way the state changes — readers never consult a stale persisted field alone, they always merge it with the marker scan. Centralize the scan in one helper so every consumer (enumeration, query tools, extraction, recovery) computes effective state the same way.

## Rationale

The session file is the single source of truth ([ADR-014](../architecture/ADR-014-session-source-of-truth.md)), and a `branch_summary` (or any custom entry) cannot be edited after it is written. Modeling "this branch was extracted", "this step ran", or "this summary was reversed" therefore cannot be a field flip on the entry — it must be an append. A marker entry is the append-only analog of a flag: it records the transition without touching the immutable record, and scanning markers at read time computes the current state the same way recovery does (derive what to do from observable on-file state, not a tracked boolean — the same posture as [DES-006](DES-006-state-based-migration-detection.md)). One scan shared across every consumer keeps the effective-state rule in one place, so a new reader cannot forget to apply it.

## Examples

### Do This

```
// append-only: record the transition with a marker naming the target
appendState(session, COMPLETION_MARKER, { kind: "branch-extracted", branchId })

// read time: scan markers ONCE, compute effective state through one helper
reversed = scanReversalMarkers(session)            // Set<summaryEntryId>
isDone(branchId)            = completionMarkers(session).has(...)
effectiveKind(summaryId, persistedKind) =
    summaryId in reversed ? "reversed" : persistedKind
```

**Why**: The immutable entry is never touched; the marker is monotonic and restart-safe (it reloads with the file). Every consumer goes through the one helper, so "extracted", "done", and "reversed" are each defined in exactly one place. A crash mid-mark leaves either no marker (re-runs) or a marker (skips) — both converge.

### Don't Do This

```
// try to mutate the immutable entry's persisted kind to "reversed"
summaryEntry.details.kind = "reversed"   // impossible — entries are append-only

// or maintain a parallel in-memory map of reversed ids
reversedIds: Map<...>                      // lost on restart; a second source of truth
```

**Why**: The session tree exposes no mutation primitive, so the field-flip cannot be done at all; and an in-memory map is lost on restart and drifts from the file (the dual-source-of-truth problem ADR-014 removed). Consumers reading the persisted `kind` directly (instead of through the helper) would each re-derive the rule and inevitably disagree.

## Exceptions

- State that is genuinely latest-wins scalar (e.g. the current topic base, the active checkpoint) rides a **snapshot** entry whose last append is authoritative (`boomerang-state`) — that is a different append-only idiom, not a marker set, and does not need a scan.
- A marker whose target id can be reused or recycled needs a stable, unique target key (entry ids, not positions) so a marker never attaches to the wrong entity after a rewrite.

---

## Related

- See also: [ADR-014](../architecture/ADR-014-session-source-of-truth.md) — the session file as the append-only source of truth that makes this pattern necessary
- See also: [DES-006](DES-006-state-based-migration-detection.md) — the companion "derive state from observable on-file reality, not a tracked flag" idiom (state-presence detection)
- Related feature: [../feature-designs/boundary-detection.md](../feature-designs/boundary-detection.md) — the `reversed` marker + `effectiveKind` discriminator that orphans a summary a `/rollback` reversed
- Related feature: [../feature-designs/memory.md](../feature-designs/memory.md) — the `extracted` branch markers and `step-done` pipeline markers that make trunk-close extraction idempotent and crash-recoverable
- Implementation: `src/sessions/trunk.ts` — `markBranchExtracted`/`isBranchExtracted`, `markStepDone`/`isStepDone`, `markReversed`/`effectiveKind`, the shared `getBranchRecords` kind-filter chokepoint
