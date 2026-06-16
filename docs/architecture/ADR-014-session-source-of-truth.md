# ADR-014: Session file as the conversational source of truth

**Status**: Accepted
**Date**: 2026-06-15
**Supersedes**: ADR-001 (the "registry = source of truth" portion of its Decision)

## Context

[ADR-001](ADR-001-agent-sdk.md) chose the pi agent SDK and recorded that the SDK's `AgentSessionRuntime` is intentionally not used because "Tachikoma's source of truth is its own drizzle-backed session registry" — operational metadata (summary, `closedAt`/`lastResumedAt`, phased `postProcessingState`, channel↔message routing) "that the runtime's jsonl model has no place for," with one `AgentSession` per conversation swapped on topic boundary.

The **daily trunk session + collapsible topic branches** model replaces the topic-based session model. This changes what conversational state exists: topic shifts become `branch_summary` entries on a single daily session tree rather than separate session rows; there is no per-topic close/resume, no rolling summary, no idle-close, and no cross-session bridging. The registry's per-row operational metadata (summary, `lastExchange`, `closedAt`/`lastResumedAt`, `postProcessingState`, `error`) no longer describes the model. The pi SDK's session-tree primitives — `branchWithSummary`, append-only branches, `appendCustomEntry`/`appendCustomMessageEntry`, headless `createBranchedSession` forks, `getBranch` traversal — that ADR-001 declined to use are exactly what the trunk model needs (verified against the SDK source and a validated prototype during the trunk model's design).

## Decision

The **pi session file is the source of truth** for conversational state. Trunk/branch structure, the current topic base, branch records, and per-branch/per-step idempotency markers live as pi custom entries on the session file; injected cross-branch context uses `appendCustomMessageEntry`. The small amount of state the file cannot serve efficiently lives in the kept `app_state` key-value store (the active-trunk pointer `{ sessionFile, day, openedAt }` and an `unclosed` trunk index) and in a slimmed, extension-owned `channel_messages` routing table (`messageId → { treeEntryId, branchId }`).

Consequently:
- The `sessions` table (`src/db/core-schema.ts`) and `src/sessions/registry.ts` are **removed**.
- pi's session-tree primitives are **adopted** (this reverses ADR-001's "bypass the session tree" stance), while `AgentSessionRuntime` itself remains unused — the coordinator still drives the single-active invariant and constructs sessions through `AgentManager.open`.
- `app_state` (KV) is kept; `channel_messages` is kept but slimmed and joined to the retention sweep.

## Consequences

### Positive
- Single source of truth — no DB↔file dual-write drift; conversational state reloads natively from the file.
- The trunk model's collapse/branch/lookup mechanics map directly onto SDK primitives instead of being reconstructed in a registry.
- No migration of live session rows: the new state is created on the file going forward.

### Negative
- Trunk *discovery* needs the `app_state` pointer (the pi header is not extensible and `sessionsDir` is shared with fork/side-run sessions, so directory scanning is unreliable).
- Crash-safe close/recovery relies on on-file completion markers plus the `app_state` `unclosed` index rather than a `postProcessingState` column.
- The pre-1.0 pi session-tree surface is now load-bearing; it is tracked in `docs/reference/pi-sdk-notes.md` and re-verified on upgrade (the ADR-001 churn risk now extends to the tree primitives).

## Alternatives Considered

- **Keep the registry, reshape it per-trunk**: retains a DB↔file dual source of truth — the exact duplication this delta removes; rejected.
- **Directory-scan discovery instead of the `app_state` pointer**: unreliable because `sessionsDir` mixes trunk, fork, side-run, and shadow-fork sessions; rejected in favor of an explicit pointer.
