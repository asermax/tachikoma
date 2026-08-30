# DES-012: Namespace Sweep over Tracked Creation for Throwaway Resources

**Scope**: Project-wide
**Date**: 2026-08-30
**Last Updated**: 2026-08-30

## Pattern

When a run creates **throwaway resources inside a namespace the feature owns** — a directory under a deterministic temp location, a ref namespace like `skill-evolution/*`, a file glob inside a store — recover by **sweeping the whole namespace idempotently**, not by tracking what this run created. The sweep:

- runs from a `finally` (so it executes on every exit path) **and** on the no-work path (so a run that created nothing still sweeps — that is what heals a previous crashed run's orphans)
- is safe to run when the namespace is empty (a no-op, not an error)
- uses stable, derivable locations (a deterministic tmp dir, a fixed ref prefix) so the *next* run finds *this* run's orphans across process restarts
- follows a fixed order when teardown has dependencies (worktrees before the branches they hold checked out, then `worktree prune` for administrative residue)

The precondition is ownership: the sweep deletes **anything** in the namespace, so the namespace must be one the feature documents as its own (the `skill-evolution/<skill>-<slug>` branch-name rule does this).

Two flavors recur: **ownership-of-created-resources** (worktrees and local branches of a proposal run, `sweepProposalArtifacts`) and **hygiene-of-contents** (blank `.md` files in a wiki store, `sweepEmptyMarkdown` — which preserves structural files via a preserve list). Both are the same rule: correctness comes from re-deriving "what can be cleaned" from the namespace itself, not from a record of what happened.

## Rationale

Creation tracking is a second source of truth that dies with the process: a run that crashes mid-flight cannot clean up after itself, and the tracking state itself can be lost mid-crash — at exactly the moment it is needed. A namespace sweep's correctness does not depend on what happened; the invariant ("no stray worktrees or local branches survive") holds after any failure with no bookkeeping to maintain. This is the cleanup-shaped instance of deriving state from observation rather than tracked flags ([DES-006](DES-006-state-based-migration-detection.md)).

## Examples

### Do This

```
// sweep the owned namespace, ordered, from finally — and on the no-work path
try {
  if (eligible.length === 0) return sweepProposalArtifacts(...)   // heals prior orphans too
  await propose(...)                                              // creates worktrees + branches
} finally {
  await sweepProposalArtifacts(...)                               // worktrees → branches → prune
}
```

**Why**: Every full pass sweeps exactly once regardless of how many resources were created, whether the agent reported, or where it died — the guarantee is unconditional and trivially testable.

### Don't Do This

```
const created: Worktree[] = []                                    // ❌ tracked state dies with the crash
try { created.push(await addWorktree(...)) } finally { created.forEach(remove) }

const dir = await fs.mkdtemp("evolution-")                        // ❌ orphaned by construction —
                                                                  //   nothing later knows where to sweep

await side.run({ ..., cleanupInstruction })                       // ❌ agent-side cleanup — the agent
                                                                  //   that died can't clean up after itself
```

**Why**: Tracked cleanup only covers the paths that update the list; a fresh temp dir has no future sweeper; and agent-side cleanup fails precisely when cleanup is needed most.

## Exceptions

- **Retention-by-age sweeps** (one-shot task retention, transcript pruning) are a different pattern: they need timestamps and a policy window, not ownership. Do not force them into this shape.
- A **user-created item inside the namespace** is deleted with everything else — that is why the namespace must be documented as feature-owned, not silently assumed.

## Related

- See also: [DES-006](DES-006-state-based-migration-detection.md) — derive work/state from observation, not tracked flags (this DES is its cleanup instance)
- See also: [DES-013](DES-013-markdown-wiki-store.md) — the empty-page sweep is the wiki store's hygiene instance
- Pattern rule: [DES-002](DES-002-extension-authoring.md) — the shared markdown sweep lives in the neutral `src/util/markdown-store.ts`
- Related feature: [skill-evolution](../feature-designs/skill-evolution.md) — the "host-side `finally` sweep" decision
- Implementation: `src/extensions/skill-evolution/verify.ts` (`sweepProposalArtifacts`), `src/util/markdown-store.ts` (`sweepEmptyMarkdown`)
