# DES-014: Two-Tier Agent-Facing Feature Documentation

**Scope**: Project-wide
**Date**: 2026-08-31
**Last Updated**: 2026-08-31

## Pattern

Agent-facing documentation is delivered in **two tiers**. The **inline tier** is what stands in every session's context: the core base prompt's conversation-substrate block (`buildMainSystemPrompt`) and one lean `usage.ts` constant per extension, each injected once per session as a hidden message via `provideContext(<constant>, "<name>-usage")`, scoped by `sessionScopes`. An inline section opens with a `## <Topic>` heading, carries only what the feature **is**, **when to reach for it**, and its **critical safety rules**, and closes with its pointer line(s) — one `referencePointer` line per reference topic it names. Everything else (lists, edge cases, config knobs, turn-shape and mechanics detail) lives in the **reference tier**: a `<topic>.md` page in a `references/` directory beside the owning module (`src/extensions/<name>/references/`, `src/agent/references/`), named by `referencePointer(moduleDir, topic)` (`src/agent/prompt-references.ts`) — `Details: <abs path> (read on demand)` — the same progressive disclosure skills use. Reference pages are non-TS assets mirrored into `dist/` by `scripts/copy-assets.mjs`, since tsc emits only JS.

Two structural rules govern placement:

- **Ownership** — the core prompt and `src/agent/references/` document only the conversation substrate the coordinator/channel core owns (mid-exchange steering, `/queue`, command routing, system-origin turns, delivery timing) and never name an extension's tool or turn format, so the core stays correct regardless of which extensions are enabled. Extension-specific guidance belongs to the owning extension's section or reference.
- **Scope matching** — a section describes only what its `sessionScopes` can call; never describe a tool to an agent that cannot call it.

Drift is caught mechanically, not in review: `tests/agent/prompt-references.test.ts` enforces the ≤10,500-character budget over the complete static inline set, measured as content (each pointer line's checkout-root prefix canonicalized before measuring, so the gate bounds what is written, not where the repo is cloned), bidirectional pointer/reference integrity (every pointer resolves, every reference page is pointed to), the extension-surface deny-list over `src/agent/references/*`, and enumeration of every `usage.ts` on disk. Where each surface lands is recorded, with tier/scope/rationale, in the placement matrix in [../feature-designs/foundational-context.md](../feature-designs/foundational-context.md) — the canonical record a new agent-facing surface updates (usage constant + reference page + matrix row + enumeration entry).

## Rationale

Every character of inline guidance is paid for in every turn of every session: it competes with the conversation for attention and pushes older context toward compaction. Most feature detail, though, is needed only occasionally — the same insight that makes skills load their `SKILL.md` on demand. The two-tier split keeps the always-present surface small (what is this, when do I reach for it, what must I never get wrong) while keeping the full detail one file-read away. The ownership rule is what keeps the split maintainable: a core prompt that described extension behavior would drift the moment an extension is disabled, renamed, or configured away, and would re-create the feature-coupled core the extension architecture exists to avoid ([DES-005](DES-005-base-prompt-ownership.md)). The size budget turns "keep it lean" from a review-time judgment into a gate: if coverage pushes past it, inline sections slim further and the matrix records the cut.

## Examples

### Do This

Adding a feature with an agent-facing surface — four pieces:

```ts
// src/extensions/<name>/usage.ts — the lean inline tier
export const NAME_USAGE = `## Name

What it is and when to reach for it — plus the critical safety rule, inline.

${referencePointer(import.meta.dirname, "name")}`; // "Details: …/references/name.md (read on demand)"
```

```ts
// src/extensions/<name>/index.ts — registered like any section, scoped to what it documents
app.agent.use(provideContext(NAME_USAGE, "name-usage"), { sessionScopes: ["main"] });
```

- `src/extensions/<name>/references/name.md` carries the detail (lists, edge cases, config knobs).
- One row in the placement matrix (`docs/feature-designs/foundational-context.md`) records the tier/scope decision with rationale; the enumeration test fails until the constant is added to `STATIC_SECTIONS`.

### Don't Do This

```ts
// Growing the inline section with tool detail or turn formats — the budget test forces this into the reference
const NAME_USAGE = `## Name
... \`create_thing\` parameters, the \`📬 Thing created:\` turn shape, retention windows ...`;
```

```md
<!-- src/agent/references/conversation.md — naming an extension's surface in a core reference -->
Scheduled work arrives as a `📋 Scheduled task:` turn — call `respond_to_task` to reply.
```

**Why**: The first bloats every session with occasionally-needed detail; the second couples the core prompt to the tasks extension's turn format (deny-list test fails).

## Exceptions

Some surfaces get no inline section at all (recorded as "Omitted" rows in the matrix): guidance that is self-evident from the tool's parameter schema (bash's required `description`), features with no agent-facing surface (command pass-through, which is handled before the agent), and identity files that *are* the prompt (SOUL/USER). Dynamic providers (memory indexes, the projects snapshot) are content-as-state, not static guidance — they are covered by the matrix, not the static sweep or the budget.

## Related

- See also: [DES-001](DES-001-unified-extension-api.md) — the `app.agent.use(provideContext(...))` seam every inline section flows through
- See also: [DES-005](DES-005-base-prompt-ownership.md) — the core half of the ownership rule: what a base prompt is and where it lives
- See also: [DES-002](DES-002-extension-authoring.md) — the extension layout `usage.ts` / `references/` slot into
- Canonical record: [../feature-designs/foundational-context.md](../feature-designs/foundational-context.md) — the placement matrix (one row per agent-facing surface)
