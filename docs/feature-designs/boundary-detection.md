# Design: Boundary Detection

<!-- This design describes the current implementation approach. Updated through delta reconciliation. -->

**Feature Spec**: [../feature-specs/boundary-detection.md](../feature-specs/boundary-detection.md)
**Status**: Current

## Purpose

Explains how the topical boundary of the daily trunk is built: a shadow-fork classifier that decides whether a message continues, shifts, parks a checkpoint, or folds a tangent; the append-only branch-collapse machinery that records topics and tangents as `branch_summary` entries; the checkpoint and rollback flows that park and reverse side conversations; and the `ask_branch` tool that recovers a prior branch's full context. Why each piece is shaped the way it is, and why everything stays append-only on the session file ([ADR-014](../architecture/ADR-014-session-source-of-truth.md)).

## Problem Context

pi keeps one in-process `AgentSession` per conversation. The daily-trunk model ([ADR-014](../architecture/ADR-014-session-source-of-truth.md)) makes that session a single append-only tree for the whole day: topic shifts are `branch_summary` entries, and all conversational state (the current base, branch records, the active checkpoint, the last automatic decision, idempotency markers) lives on the file as pi custom entries — no `sessions` table, no DB↔file dual source of truth. Deciding when a topic changes — and, with checkpoints, when a tangent should be parked or returned from — is host territory; pi provides the tree primitives (`branchWithSummary`, `branch`, `appendCustomEntry`) but no policy.

**Constraints:**
- Classification must add little latency and must never block a message (fail-open)
- The classifier runs on a detached shadow fork and cannot read the live trunk's state, so checkpoint state must be injected into it
- The tree is **append-only**: there is no delete/undo primitive — `branch(id)` re-seats the leaf backward, `branchWithSummary(id, summary)` appends a summary and re-seats (verified against `docs/reference/pi-sdk-notes.md`); rollback is built on that re-seat, not on deletion
- A `branch_summary` entry cannot be mutated after the fact, so a reversed summary is orphaned by a marker whose effect is computed at read time

**Interactions:**
- The coordinator owns the middleware chain, the live trunk, and the close pipeline (see [conversation-loop](./conversation-loop.md)); the boundary middleware drives collapse on the trunk the coordinator hands it via `TrunkInbound`
- Side-channel LLM calls go through `SideRunner` on the `classifier`/`processor` tiers (see [agent-integration](./agent-integration.md))
- The trunk-close memory pipeline extracts every topic branch — tangents and reversed summaries must be excluded (see [memory](./memory.md))

## Design Overview

Two cooperating pieces, plus checkpoint/rollback/lookup machinery:

```
exchange ──> boundary inbound middleware (runs before the message is answered)
   ├─ boundary:skip / no trunk          ──> next()  (system injections never shift)
   ├─ /checkpoint, /back, /rollback     ──> manual command (handled + ack)
   ├─ forcedBranchId                    ──> same branch: append; earlier: collapse + inject pointer
   ├─ forceNew (/new <arg>)             ──> collapse live branch, next()
   └─ classifyShift (shadow fork)       ──> continue | shift | set-checkpoint | summarize-to-checkpoint
        continue:               next()
        shift:                  collapseCurrentTopic ── (related-branch pointer) ── next()
        set-checkpoint:         setCheckpoint + record lastAutoDecision + header ── next()
        summarize-to-checkpoint: summarizeCurrentTangent + header ── next()
```

A topic shift calls `collapseCurrentTopic` (`src/extensions/boundary/collapse.ts`): a `processor`-tier completion summarizes the branch's own turns, then `branchWithSummary(base, summary, details)` appends a `branch_summary` (tagged `tachikoma-branch-summary`, `kind: "topic"`, the abandoned leaf recorded as `originalLeafId`) and re-seats the leaf onto it. A tangent fold (`summarizeCurrentTangent`) reuses the same primitive with the **checkpoint** as the branch point and `kind: "tangent"`, so the leaf returns to the main line rather than advancing to a new topic. The `kind` discriminator (`topic`/`tangent`/`reversed`) is the single field consulted by the topic-branch enumeration, `ask_branch`, related-branch matching, and memory extraction — one filter keeps `topic-N` ids clean and parks tangents and reversed summaries away.

`classifyShift` (`src/extensions/boundary/classifier.ts`) forks the live branch into a throwaway session on the `classifier` tier and asks it which decision fits, returning a free-form JSON object parsed tolerantly. It is checkpoint-aware: the middleware injects whether a checkpoint is active, and the prompt only describes the checkpoint decision valid for the current state. The `set-checkpoint` criterion is a short, **unrelated**, self-contained side request (e.g. an expense logged mid-workflow); `shift` is reserved for a substantive new main topic, so the two 'unrelated' decisions stay distinct. Anything unrecognized fails open to `continue`.

`/rollback` (`src/extensions/boundary/rollback.ts`) reverses the most-recent *automatic* `set-checkpoint`/`shift` by reading `lastAutoDecision` from the trunk, rewinding the leaf to the pre-decision tip via `branch(id)` (the wrong-framing exchange goes off-path, not deleted), applying the opposite transition (collapse-as-topic / set-checkpoint), marking any orphaned summary `reversed`, and handing the triggering message to the coordinator's `replay()` to be re-answered under the corrected framing.

## Components

### Implementation Structure

| Component | Responsibility | Key Decisions |
|-----------|----------------|---------------|
| `src/extensions/boundary/index.ts` | Wiring: `enabled` + the two per-result kill-switches; registers `ask_branch` (main scope) and the inbound middleware; the middleware applies the metadata fast-paths, manual commands, forced routing, and classifier results | The middleware is registered unconditionally; `enabled` only gates the classifier. Order matters: `boundary:skip` and the manual commands precede the classifier so system injections never shift and manual control always works. Auto `set-checkpoint`/`shift` record `lastAutoDecision` and set a turn-scoped decision header exactly where the decision is made, so the header and the rollback target can never disagree |
| `src/extensions/boundary/classifier.ts` | `classifyShift`: shadow-fork classification, tolerant parsing, fail-open | Free-form JSON prompt + tolerant `parseDecision` (first balanced object → `decision`), not a structured-output schema; `checkpointActive` injected, prompt is conditional and conservative; unknown/unparseable ⇒ `continue` |
| `src/extensions/boundary/collapse.ts` | `collapseCurrentTopic` (topic) and `summarizeCurrentTangent` (tangent): summarize the branch via a side completion, collapse it, write boomerang-state | One summary prompt + transcript renderer serves topic and tangent; topic collapse clears any active checkpoint and preserves `lastAutoDecision`; both degrade gracefully (return null, leave state intact) |
| `src/extensions/boundary/commands.ts` | `/checkpoint`, `/back` manual handlers | Detected before the classifier via the channel-stamped `metadata.command` token (`isCommand`, with an exact-text fallback), so they never reach the classifier or its "Checking conversation topic…" lead-in — robust to a reply quote or trailing argument prepended to `text` (which would defeat an exact match); mark the message handled and ack immediately — no agent turn, so the decision label is in the ack, not a streamed header |
| `src/extensions/boundary/rollback.ts` | `/rollback`: eligibility + immediacy, rewind, opposite transition, `reversed` marker, replay | Shares `isCommand` recognition with the other manual commands; immediacy = exactly one user-message turn after the decision marker (counting user messages, not entries, so an appended summary isn't read as a new turn); `handled` set unconditionally so the command text never reaches the agent |
| `src/extensions/boundary/related.ts` | `findRelatedBranch` + `injectRelatedBranchContext` | LLM matcher over prior branch summaries; on a clear match injects one hidden pointer (last exchange + `ask_branch` hint); fails soft to null |
| `src/extensions/boundary/ask-branch.ts` | `ask_branch` tool: headless-fork a prior branch's full conversation and answer a focused question | Resolves through the topic-branch enumeration so tangents/reversed are never targets; reports gracefully for unknown ids |
| `src/sessions/trunk.ts` | Trunk conversational state: `BoomerangState` (base, checkpoint, last auto decision), branch-record enumeration with the kind filter, `reversed` markers, completion markers | `getBranchRecords` is the kind-filter chokepoint (excludes `tangent` + `reversed`); `getAllBranchRecords` (unfiltered) for the tangent counter and rollback target lookup; checkpoint/decision lifecycle are patch-merges over the latest boomerang snapshot |
| `src/agent/session-tree.ts` | Typed wrappers over pi's `SessionManager`: `branchWithSummary`, `collapseTangent`, `reseatLeaf`, `branchEntriesSinceBase`, `checkpointHasTangent` | The single seam over the SDK; `collapseTangent` reuses `branchWithSummary` with `kind: "tangent"` and the checkpoint as the branch point |

### Cross-Layer Contracts

**`TrunkInbound`** (coordinator → middleware, `src/extensions/api.ts`): a read-only snapshot of the live trunk — `session`, `sessionFile`, `currentBaseId`, `branchRecords` (topic-filtered), `liveBranchId`, `hasAssistantTurnSinceBase`, `checkpointId`, `lastAutoDecision`. The middleware reads transition inputs from it and writes by calling trunk helpers directly on `session` (the snapshot is read-only).

**Decision header** (middleware → coordinator → channel): a middleware sets `message.metadata.decisionHeader = { label, note, rollbackable }`; the coordinator forwards it turn-scoped to `channel.respond({ header })`. Manual commands ack directly and never set it.

**Replay** (rollback → coordinator): `app.sessions.replay(text, header)` re-runs the triggering message as a fresh system-origin turn (`origin: "system"`, `boundary: "skip"`) that bypasses `submit()` — so it skips pending-input, prefix-stripping, steering, and re-classification — and carries the rollback header.

## Key Decisions

### Detection as inbound middleware, not coordinator logic

**Choice**: Boundary detection is an `app.inbound.use` middleware inside the `boundary` extension that drives transitions directly on the trunk session.
**Why**: [DES-001](../design/DES-001-unified-extension-api.md) makes inbound middleware the place where "boundary detection decides to continue or shift"; the `InboundContext.trunk` contract is exactly the leverage needed, and the coordinator stays free of topic semantics. Driving collapse directly on the live session (rather than through a coordinator callback) is what makes checkpoint/rollback tree surgery possible.
**Alternatives Considered**: Coordinator-owned detection (couples topic policy into core); pi's `input` event (session-scoped, cannot orchestrate branch transitions).
**Consequences**: Pro — deleting the extension removes the feature cleanly; disabling is a config flag; testable with a faked `SideRunner`. Con — the middleware depends on the coordinator handing it the live trunk.

### Shadow-fork classification, fail-open

**Choice**: `classifyShift` forks the live branch into a throwaway headless session (`shadowFork`, `classifier` tier), asks it which decision fits, and parses a free-form JSON reply tolerantly. Any error or unrecognized value degrades to `continue`.
**Why**: The classifier must never block a message or mutate the live session. A shadow fork gives the model the real conversation context without touching the trunk; tolerant parsing (first balanced object → `decision`) avoids brittle structured-output assumptions. A misclassification costs at most one wrong branch, which `/rollback` can reverse.
**Alternatives Considered**: A structured-output schema (the SDK's shadow fork takes only a text prompt, so there is no schema to force; a future SDK structured-output capability would switch this); classifying against a flattened transcript blob (lossy).
**Consequences**: Pro — non-invasive and resilient. Con — detection *quality* is empirical; the conservative prompt and per-result kill-switches bound the over-trigger risk.

### One checkpoint-aware classification call

**Choice**: A single `classifyShift` call returns one of four results. The prompt is **conditional** on whether a checkpoint is active (injected by the middleware, since the shadow fork cannot read the live trunk): it describes `set-checkpoint` only when none is active and `summarize-to-checkpoint` only when one is. The middleware gates defensively on top of the prompt.
**Why**: The classifier needs to choose among all valid transitions for the message; gating the prompt to the current state keeps it conservative (only one checkpoint decision is ever offered) and shrinks the wrong-decision space. One call covers shift and both checkpoint transitions.
**Consequences**: Pro — one classifier round-trip per classified message; the valid checkpoint decision is unambiguous. Con — the middleware must inject `checkpointActive` truthfully (it owns that state).

### Checkpoint rides boomerang-state, not a new entry type

**Choice**: The active checkpoint (`checkpointId`) and the last automatic decision (`lastAutoDecision`) ride the existing `boomerang-state` custom entry — the latest-wins snapshot already re-read each inbound and surviving restart. Clearing is an append (a `null`/override snapshot), never a deletion.
**Why**: ADR-014 makes the session file the source of truth and the boomerang snapshot already carries the current base; checkpoint and auto-decision state are small, conversational, and restart-safe exactly the way `currentTopicBaseId` is. Reusing it avoids a new persistence surface and dual-write.
**Alternatives Considered**: A dedicated entry type / table (a new surface for no gain); a pi label (carries no queryable id, too thin for "one active, override, cleared-on-shift").
**Consequences**: Pro — restart-safe, no migration; consistent with the append-only model. Con — partial updates must re-merge the whole snapshot (latest-wins), so lifecycle helpers patch-merge over the prior snapshot.

### Summarize reuses `branchWithSummary` with the checkpoint as the branch point

**Choice**: `/back` and `summarize-to-checkpoint` call `collapseTangent`, which is `branchWithSummary(checkpointId, summary, { kind: "tangent", … })`.
**Why**: `branchWithSummary` takes an arbitrary branch point and re-seats the leaf onto the new summary rooted there. Using the checkpoint as that point folds the tangent into a summary rooted at the main-line tip, so the leaf returns to the main line — exactly the "park the main line, fold only the tangent" property. One primitive serves topic collapse and tangent collapse.
**Alternatives Considered**: A bespoke tangent collapse (reinvents the primitive); folding the tangent into the prior topic summary (would collapse the main line). An **empty-tangent guard** short-circuits before the primitive when no conversational turn follows the checkpoint (`checkpointHasTangent` walks the leaf path for a real message — robust to the boomerang append that advances the leaf past the checkpoint message), so no vacuous summary is ever created.

### `kind` discriminator with a separate `tangent-N` sequence

**Choice**: `BranchSummaryDetails.kind` is `"topic"` (default) or `"tangent"`; `"reversed"` is never persisted — it is computed at read time from a reversal marker. The topic-branch enumeration (`getBranchRecords`) excludes both `tangent` and `reversed`, so `ask_branch`, related-branch matching, and memory extraction skip them in one place; tangents count on their own `tangent-N` sequence so `topic-N` ids stay stable.
**Why**: Without a discriminator, tangents would interleave into the topic sequence and become branch-query targets and extraction subjects. A kind field keeps the topic sequence clean and makes exclusion a single filter; computing `reversed` at read time is the only option because a `branch_summary` cannot be mutated (append-only) — the reversal marker + `effectiveKind` read-time computation are an instance of [DES-008](../design/DES-008-marker-computed-effective-state.md).
**Consequences**: Pro — one filter, four consumers; stable ids. Con — every branch-record consumer must go through the chokepoint (a shared helper centralizes it).

### Rollback = `branch(id)` re-seat + opposite transition + replay (no deletion)

**Choice**: `/rollback` rewinds via `reseatLeaf(preDecisionLeafId)` (the sanctioned "move leaf to an earlier entry" primitive — `session.sessionManager.branch(id)`), applies the opposite transition (collapse-as-topic for a reversed `set-checkpoint`; set-checkpoint + mark `reversed` for a reversed `shift`), then hands the triggering message to `replay()` to be re-answered under the corrected framing.
**Why**: The tree is append-only — there is no delete/undo. The user's "roll it back" intent is satisfied by `branch(id)`, which moves the active tip backward and leaves the wrong-framing exchange as an inert off-path branch (not re-extracted, since off-path branches are not extraction targets). This keeps everything append-only and restart-safe while giving the user a correctly-framed fresh answer.
**Alternatives Considered**: File truncation to delete the wrong-framing exchange (desyncs the live session's in-memory index, breaks the append-only/idempotent-replay model ADR-014 relies on, brittle across upgrades); reclassifying the old summary without rewind+replay (rejected — the user wants the message re-answered under the corrected framing, which requires the rewind + replay).
**Consequences**: Pro — fully append-only; restart-safe; reuses sanctioned primitives. Con — the wrong-framing exchange physically remains as an off-path dead branch (inert). Immediacy is "exactly one user turn after the decision marker" so a later summarize (which appends a summary, not a user turn) is not misread as disqualifying.

### Automatic decisions recorded on the tree (`lastAutoDecision`)

**Choice**: Each automatic `set-checkpoint`/`shift` records `lastAutoDecision = { kind, preDecisionLeafId }` in the boomerang snapshot; `/rollback` reads the latest snapshot. Only automatic decisions are recorded — manual `/new`, `/checkpoint`, `continue`, and `summarize-to-checkpoint` are not rollback targets.
**Why**: `/rollback` must survive a restart between the bad decision and noticing it. Riding the boomerang snapshot is restart-safe with no new surface. The triggering message is read from the tree at rollback time (it does not exist at classification time), and immediacy is verified by a tree walk — so `preDecisionLeafId` (the leaf before the triggering exchange) is all that must be stored.
**Consequences**: Pro — restart-safe; the header (set in the same block) and the rollback target can never disagree. Con — an extra field on the snapshot (negligible).

### Auto-detection conservative-on, with per-result kill-switches

**Choice**: `set-checkpoint`/`summarize-to-checkpoint` ship **on by default**, with prompts biased against parking and two per-result flags `autoSetCheckpoint`/`autoSummarizeToCheckpoint` (default `true`) so each can be disabled independently without losing manual control or topic detection.
**Why**: The two results are must-haves and are already gated by `enabled`; conservative-on gives the feature immediately while bounding the over-trigger risk, and the kill-switch lets an operator disable auto-checkpointing alone. Detection quality is empirical (a tuning pass may follow).
**Consequences**: Pro — capability ships; risk bounded. Con — over/under-triggering is the known residual risk until tuning.

## System Behavior

### Scenario: Topic shift mid-conversation

**Given**: An active trunk whose live branch has an assistant turn
**When**: The classifier returns `shift`
**Then**: The branch is summarized and collapsed into a `branch_summary` (`kind: "topic"`), the leaf re-seats onto the summary, the summary becomes the new base, and a "🆕 New topic" header (advertising `/rollback`) is set on the response. If a prior branch clearly matches the new message, a pointer to it is injected.

### Scenario: A side tangent, parked and returned

**Given**: An active trunk at a main-line tip `C`
**When**: The user sends `/checkpoint`, exchanges a tangent, then `/back`
**Then**: `C` is recorded as `checkpointId`; on `/back` the tangent turns are folded via `collapseTangent(C, …)`, the leaf re-seats onto the summary rooted at `C`, `checkpointId` clears, and the main line resumes at `C` — never collapsed. A "↩️ Summarized to checkpoint" ack is shown.

### Scenario: Auto set-checkpoint then summarize-to-checkpoint

**Given**: A checkpoint is not active and the classifier detects a short side task beginning
**When**: It returns `set-checkpoint`
**Then**: A checkpoint is set, `lastAutoDecision` records it, a "📌 Checkpoint set" header (advertising `/rollback`) is set, and the message streams as the first tangent turn. Later, with the checkpoint active, `summarize-to-checkpoint` folds the tangent and resumes the main line.

### Scenario: A short unrelated interruption, checkpointed and resumed

**Given**: An active evening-check-in workflow (visible in the conversation history) and no checkpoint active
**When**: The user asks to log a fuel expense to their wallet and the classifier returns `set-checkpoint`
**Then**: A checkpoint is set at the workflow tip and the expense request streams as the first side-task turn; after the wallet entry completes (2-3 turns), a later `summarize-to-checkpoint` folds it away and the evening check-in resumes — the workflow was parked, never collapsed.

### Scenario: Rollback a wrong auto topic shift

**Given**: The most-recent automatic decision was `shift` and exactly one exchange has occurred since
**When**: The user sends `/rollback`
**Then**: The leaf re-seats to the pre-decision tip, a checkpoint is set there, the auto-shift's topic summary is marked `reversed`, `lastAutoDecision` clears, and the triggering message is replayed as the first tangent turn — re-answered under the checkpoint framing. A "🔄 Rolled back to checkpoint" header is shown.

### Scenario: Rollback no-op

**Given**: The most-recent decision was manual, `continue`, or `summarize-to-checkpoint`, or there is no recent auto decision, or more than one exchange has occurred since it
**When**: The user sends `/rollback`
**Then**: It is a no-op with a "Nothing to roll back" notice — exactly two cases are reversible.

### Scenario: Classifier outage

**Given**: The classifier-tier model is unreachable or returns unparseable output
**When**: A message arrives
**Then**: `classifyShift` fails open to `continue` — the message is handled normally, no spurious transition occurs, and the loop is never blocked.

### Scenario: Recover context from a prior branch

**Given**: A focused question whose answer lives in a prior topic branch's full conversation (not its summary)
**When**: The agent calls `ask_branch` with that branch's id
**Then**: The branch's abandoned leaf is headless-forked and the question is answered from it; tangents and reversed summaries are never offered as targets.

## Notes

- The classification prompt is checkpoint-conditional and biased toward `continue` (and, for checkpoints, against parking) — the conservative posture is in the prompt, reinforced by defensive gating and the per-result kill-switches.
- `set-checkpoint` fires for a short, **unrelated** side request — the discriminator from `shift` is substantiveness (a minor side task to fold away vs. a new main topic). The classifier infers the mid-workflow/mid-conversation state from the conversation history the shadow fork already reads; no workflow flag is injected.
- The checkpoint model replaced an earlier "parked sub-session" idea during speccing — checkpoints are strictly simpler (linear, reuse `branchWithSummary`/`branch`, no sub-session juggling).
- The append-only/no-delete property is load-bearing and pi-upgrade-sensitive; it is captured in `docs/reference/pi-sdk-notes.md` and re-verified on each upgrade (per ADR-014). Rollback relies on `branch(id)` re-seat and the absence of a delete primitive.
- The turn-scoped decision header is a general channel-rendering capability owned by the [conversation loop](./conversation-loop.md) and rendered by the [Telegram channel](./telegram.md); the boundary middleware only produces the descriptor.
