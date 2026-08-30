# DES-013: Markdown Wiki-Store Conventions for Feature-Owned Knowledge

**Scope**: Project-wide
**Date**: 2026-08-30
**Last Updated**: 2026-08-30

## Pattern

A feature's accumulated knowledge lives as **wiki-style markdown in a feature-owned directory** (memory's `memories/{episodic,topics,learnings}/`, skill-evolution's `memories/skill-evolution/`), with these conventions:

- **`MEMORY.md` index** — one line per page (`- [Title](./slug.md): one-line summary`); the store's discovery surface. It is *structural even when blank*: the sweep never removes it.
- **One page per topic/pattern** — new evidence **updates the page** (dated Evidence sections where chronology matters) instead of spawning a duplicate; the index entry is rewritten when the page's one-liner changes, and never outlives the page.
- **Size caps** (~50 lines per page) enforced by a periodic maintenance pass that also merges near-duplicates — so pages stay readable and prompts that quote them stay bounded.
- **Empty-then-sweep deletion** — a page whose content was folded away becomes blank and is deleted after the run (`sweepEmptyMarkdown`, with a preserve list for other structural files).
- **Conventions are agent-enforced through shared prompt sections** — one store-conventions block reused by every prompt that touches the store, so every writer follows the same rules.
- **Deterministic facts never go through the agent** — SHAs, dates, statuses, and other bookkeeping belong in a host-written structural file (skill-evolution's `skill-impact-log.md`), never in an agent-written page.
- **Seeding is skip-if-exists** — the layout bootstrap creates dirs and structural files only when absent, so user edits survive.
- **Parsing is lenient** — the store is user-editable; malformed rows/entries are logged and skipped, never fatal.

The mechanical helpers (`listMarkdown`, `sweepEmptyMarkdown`, `isBlankMarkdown`) are shared in neutral `src/util/markdown-store.ts` so the conventions remain one implementation across stores.

## Rationale

Git-versioned markdown is simultaneously human-reviewable (the user reads and edits the store directly), diffable (history is meaningful), and agent-editable with the ordinary file tools — no schema, no migration, no parser to keep in sync. The wiki conventions are what keep such a store convergent under many writers: the index gives discovery, update-not-duplicate gives one authoritative page per topic, caps keep pages and prompts bounded, and the sweep prevents husks. Splitting agent judgment (what to write) from deterministic bookkeeping (host-written ledgers) keeps facts that must not drift out of the model's hands.

## Examples

### Do This

```
memories/<store>/
├─ MEMORY.md            # `- [Title](./slug.md): PROBLEM — ROOT CAUSE — FIX` — structural, never swept
├─ <slug>.md            # one topic/pattern; new evidence appends a dated line under Evidence
└─ <ledger>.md          # host-written deterministic facts (if the feature has any)

// host sweep after each writer run
await sweepEmptyMarkdown(storeDir, { preserve: [LEDGER_FILENAME] })
```

**Why**: Discovery, authority (one page per topic), bounded size, and no husks are each enforced by exactly one convention — and the sweep preserves the structural files that make the store navigable.

### Don't Do This

```
// a page per day/incident                                     // ❌ fragments one topic across files
// index entries left behind for swept pages                    // ❌ the index points at nothing
// an agent-written ledger of SHAs/statuses                     // ❌ LLM-written SHAs are a defect class
// strict table/entry parsing that throws on user edits         // ❌ the store is user-editable; skip, log
```

**Why**: Per-incident pages defeat the index and duplicate themes; stale index entries break discovery; agent-written deterministic facts introduce exactly the drift the ledger exists to prevent; strict parsing turns user edits into crashes.

## Exceptions

- **The episodic store** (memory) is date-stamped daily/weekly/monthly files with **no `MEMORY.md` index** — the conventions apply to *topic-shaped* stores; a date-partitioned archive is discovered by filename, not an index.
- **Host-written ledgers** (`skill-impact-log.md`) share the directory but not the agent-writing path: deterministic markdown tables, written and rewritten by host code (see [DES-010](DES-010-agent-driven-git-host-verified.md) for why the agent never writes them).

## Related

- See also: [DES-002](DES-002-extension-authoring.md) — shared helpers live in the neutral `src/util/markdown-store.ts`; each feature owns its own path helper and layout seeding
- See also: [DES-012](DES-012-namespace-sweep-cleanup.md) — the empty-then-sweep deletion is the hygiene flavor of the namespace sweep
- See also: [DES-011](DES-011-post-processing-agent-shapes.md) — the headless run shape the maintenance pass uses
- Related features: [memory](../feature-designs/memory.md), [skill-evolution](../feature-designs/skill-evolution.md)
- Implementation: `src/util/markdown-store.ts`, `src/extensions/memory/layout.ts`, `src/extensions/skill-evolution/{layout,store}.ts`
