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
   ├─ no trunk                          ──> next()
   ├─ boundary:skip, origin:system      ──> parkable main line + no checkpoint + not a replay + kill-switch on → setCheckpoint + inject focus + header; else next()  (system side tasks checkpoint, never classify)
   ├─ boundary:skip (other)             ──> next()
   ├─ /checkpoint, /back                ──> manual command: ack (no turn); with trailing text → strip text + next() (streams it as the tangent's / resumed main line's first turn, classifier skipped)
   ├─ /rollback                         ──> manual command (handled; replays the triggering turn; trailing text discarded)
   ├─ forcedBranchId                    ──> same branch: append; earlier: collapse + inject pointer + emit session:topic-changed
   ├─ forceNew (/new <arg>)             ──> startNewBranch (collapse + related pointer + emit session:topic-changed), next()
   └─ classifyShift (shadow fork)       ──> continue | shift | set-checkpoint | summarize-to-checkpoint
        continue:               next()
        shift:                  startNewBranch (collapse + related pointer + emit session:topic-changed) + record lastAutoDecision + header ── next()
        set-checkpoint:         setCheckpoint + record lastAutoDecision + inject focus + header ── next()
        summarize-to-checkpoint: summarizeCurrentTangent + header ── next()
```

`/new` and an auto-detected `shift` share one `startNewBranch` routine — same collapse summary, same "Starting a new topic" status, and the same related-branch pointer injection — so the two paths cannot drift apart. Only the auto-shift layers `/rollback` bookkeeping on top (a manual `/new` is intentionally not a rollback target, R7). Each genuine topic change emits `session:topic-changed` (defined in `src/events.ts` with a `{ reason: "auto-shift" | "/new" | "earlier-branch" }` payload) so downstream consumers reset per-branch state — the skills extension clears its proactive-injection record so the new branch re-evaluates which skills are relevant (see [skills](skills.md)). The event fires only for genuine topic changes, never for `continue`, `set-checkpoint`, or `summarize-to-checkpoint`.

A topic shift calls `collapseCurrentTopic` (`src/extensions/boundary/collapse.ts`), which delegates to the shared core primitive `collapseLiveTopicBranch` (`src/agent/branch-collapse.ts`): a `processor`-tier completion summarizes the branch's own turns, then `branchWithSummary(base, summary, details)` appends a `branch_summary` (tagged `tachikoma-branch-summary`, `kind: "topic"`, the abandoned leaf recorded as `originalLeafId`) and re-seats the leaf onto it; the boundary layer then writes the boomerang shift snapshot. That core primitive is also what the coordinator's trunk close reuses to collapse the live branch before extraction (see [conversation-loop](conversation-loop.md)) — so the summarize-and-collapse lives in core and both the topic-shift and trunk-close paths share it, per [DES-001](../design/DES-001-unified-extension-api.md). A tangent fold (`summarizeCurrentTangent`) reuses the same summary prompt and transcript renderer with the **checkpoint** as the branch point and `kind: "tangent"`, so the leaf returns to the main line rather than advancing to a new topic. The `kind` discriminator (`topic`/`tangent`/`reversed`) is the single field consulted by the topic-branch enumeration, `ask_branch`, related-branch matching, and memory extraction — one filter keeps `topic-N` ids clean and parks tangents and reversed summaries away.

`classifyShift` (`src/extensions/boundary/classifier.ts`) forks the live branch into a throwaway session on the `classifier` tier and asks it which decision fits, returning a free-form JSON object parsed tolerantly. It is checkpoint-aware: the middleware injects whether a checkpoint is active, and the prompt only describes the checkpoint decision valid for the current state. The `set-checkpoint` criterion is a self-contained side task **distinct from** the current thread (e.g. an expense logged mid-workflow, a note/reminder capture, a lookup, a background task or daily ceremony starting its own exchange) — the discriminator from `continue` is that it clearly starts a separate task rather than following on (regardless of turn count), and from `shift` that it is a minor interleaved side task rather than a substantive new main topic; the `summarize-to-checkpoint` criterion is a return to the main line — by explicit "going back" phrasing, by the message's topic matching the pre-checkpoint conversation, or (the common case) by the side task looking done and the message not following on from it but fitting the main line as its next step, even without naming it (read from the history the shadow fork already sees); `shift` is reserved for a substantive new main topic, so the two 'unrelated' decisions stay distinct and a return to the main line is never a `shift`. Anything unrecognized fails open to `continue`.

`/rollback` (`src/extensions/boundary/rollback.ts`) reverses the most-recent *automatic* `set-checkpoint`/`shift` by reading `lastAutoDecision` from the trunk, rewinding the leaf to the pre-decision tip via `branch(id)` (the wrong-framing exchange goes off-path, not deleted), applying the opposite transition (collapse-as-topic / set-checkpoint), marking any orphaned summary `reversed`, and handing the triggering message to the coordinator's `replay()` to be re-answered under the corrected framing. Unlike `/checkpoint`/`/back`, it intentionally takes **no** trailing text — any text after `/rollback` is discarded and only the original triggering turn is replayed.

## Components

### Implementation Structure

| Component | Responsibility | Key Decisions |
|-----------|----------------|---------------|
| `src/extensions/boundary/index.ts` | Wiring: `enabled` + the two per-result kill-switches; registers `ask_branch` (main scope) and the inbound middleware; the middleware applies the metadata fast-paths, manual commands, forced routing, and classifier results | The middleware is registered unconditionally; `enabled` only gates the classifier. Order matters: the null-trunk and `boundary:skip` paths precede the manual commands and the classifier so system injections never classify and manual control always works. A system-origin `skip` side task is checkpointed when a main line is parkable, no checkpoint is active, it is not a replay, and `autoSetCheckpoint` is on (issue-411); it is not a `/rollback` target (system turns aren't user messages for the immediacy counter — recovery is `/back`), so `coordinator.replay()` stamps `metadata.replay = true` to exclude rollback replays whose framing is already applied. `/new` and an auto-detected `shift` both go through a shared `startNewBranch` routine (collapse summary + related-branch pointer + `session:topic-changed` event); only the auto `shift` records `lastAutoDecision` and sets a turn-scoped decision header exactly where the decision is made, so the header and the rollback target can never disagree (a manual `/new` is not a rollback target, R7) |
| `src/extensions/boundary/classifier.ts` | `classifyShift`: shadow-fork classification, tolerant parsing, fail-open | Free-form JSON prompt + tolerant `parseDecision` (first balanced object → `decision`), not a structured-output schema; `checkpointActive` injected, prompt is conditional and conservative; the prompt also has the model judge open-vs-finished topic state from the history it already reads (issue-423), gating `set-checkpoint` on an open topic; unknown/unparseable ⇒ `continue` |
| `src/agent/branch-collapse.ts` | `collapseLiveTopicBranch`: the shared core summarize-and-collapse (LLM summary + `branchWithSummary`, `kind: topic`, records `originalLeafId`), plus the `SUMMARY_SYSTEM` prompt and transcript renderer | Core machinery ([DES-001](../design/DES-001-unified-extension-api.md)) shared by the boundary topic-shift path and the coordinator's trunk-close collapse (see [conversation-loop](conversation-loop.md)); writes no boomerang state — callers own their post-bookkeeping; degrades gracefully (return null, leave state intact) |
| `src/extensions/boundary/collapse.ts` | `collapseCurrentTopic` (topic) and `summarizeCurrentTangent` (tangent): delegate to the core primitive, then write boomerang-state (topic) / clear the checkpoint (tangent) | Topic collapse clears any active checkpoint and preserves `lastAutoDecision`; both degrade gracefully (return null, leave state intact) |
| `src/extensions/boundary/commands.ts` | `/checkpoint`, `/back` manual handlers | Detected before the classifier via the channel-stamped `metadata.command` token (`isCommand`, with a leading-text fallback `/<name>` optionally followed by a trailing argument), so they never reach the classifier or its "Checking conversation topic…" lead-in — robust to a reply quote or trailing argument prepended to `text`; `commandArgument` extracts the trailing text by anchoring to the *last* `/<name>` (the user's input, appended after any quote). Each returns `"acked"` (bare/guard-failure: mark handled, ack, no turn) or `"continue"` (trailing text: strip it onto `message.text`, leave un-`handled`, and the middleware `next()`s so it streams as the first turn of the tangent `/checkpoint` or resumed main line `/back`, classifier skipped — the same prefix-strip flow as `/new`). `/checkpoint` injects the tangent-focus instruction (R19) |
| `src/extensions/boundary/rollback.ts` | `/rollback`: eligibility + immediacy, rewind, opposite transition, `reversed` marker, replay | Shares `isCommand` recognition with the other manual commands; immediacy = exactly one user-message turn after the decision marker (counting user messages, not entries, so an appended summary isn't read as a new turn); `handled` set unconditionally so the command text never reaches the agent. It keeps the boolean contract and discards any trailing text — only the original triggering message is replayed (the trailing-text feature is `/checkpoint`/`/back` only) |
| `src/extensions/boundary/related.ts` | `findRelatedBranch` + `injectRelatedBranchContext` | LLM matcher over prior branch summaries; on a clear match injects one hidden pointer (last exchange + `ask_branch` hint); fails soft to null |
| `src/extensions/boundary/focus.ts` | `setCheckpointAndFocus` / `injectTangentFocus`: set a checkpoint and append the hidden tangent-focus instruction in one step | Mirrors `injectRelatedBranchContext` — a `custom_message` (`display:false`, in LLM context) appended right after the checkpoint is set, so it rides every tangent turn. `setCheckpointAndFocus` is the single entry point called from every checkpoint-set site (manual `/checkpoint`, auto `set-checkpoint`, the system-origin side-task checkpoint, and rollback Case B), so the focus pairing is structural and no site can forget it. Type `custom_message` is skipped by `checkpointHasTangent` and `renderBranchTranscript` and parked by `collapseTangent`, so it neither trips the empty-tangent guard nor pollutes/persists in the tangent summary (issue-411) |
| `src/extensions/boundary/ask-branch.ts` | `ask_branch` tool: headless-fork a prior branch's full conversation and answer a focused question | Resolves through the topic-branch enumeration so tangents/reversed are never targets; reports gracefully for unknown ids |
| `src/sessions/trunk.ts` | Trunk conversational state: `BoomerangState` (base, checkpoint, last auto decision), branch-record enumeration with the kind filter, `reversed` markers, completion markers | `getBranchRecords` is the kind-filter chokepoint (excludes `tangent` + `reversed`); `getAllBranchRecords` (unfiltered) for the tangent counter and rollback target lookup; checkpoint/decision lifecycle are patch-merges over the latest boomerang snapshot |
| `src/agent/session-tree.ts` | Typed wrappers over pi's `SessionManager`: `branchWithSummary`, `collapseTangent`, `reseatLeaf`, `branchEntriesSinceBase`, `checkpointHasTangent` | The single seam over the SDK; `collapseTangent` reuses `branchWithSummary` with `kind: "tangent"` and the checkpoint as the branch point |

### Cross-Layer Contracts

**`TrunkInbound`** (coordinator → middleware, `src/extensions/api.ts`): a read-only snapshot of the live trunk — `session`, `sessionFile`, `currentBaseId`, `branchRecords` (topic-filtered), `liveBranchId`, `hasAssistantTurnSinceBase`, `checkpointId`, `lastAutoDecision`. The middleware reads transition inputs from it and writes by calling trunk helpers directly on `session` (the snapshot is read-only).

**Decision header** (middleware → coordinator → channel): a middleware sets `message.metadata.decisionHeader = { label, note, rollbackable, reaction? }`; the coordinator forwards it turn-scoped to `channel.respond({ header })`. When `reaction` is set the channel places that emoji on the bot's own message (Telegram: a `setMessageReaction`) instead of rendering the italic label text, with the label/note retained as a text fallback. `BOUNDARY_REACTIONS` (`src/extensions/boundary/reactions.ts`) holds the single mapping (new topic 🔥, checkpoint set 💔, summarized to checkpoint ❤, rolled back 👻) shared by every boundary site so an auto and a manual occurrence of the same boundary agree; only Telegram-valid reaction emoji are used — the original banner glyphs (🆕 📌 ↩️ 🔄) are not valid reactions and are rejected by the API. Manual commands ack via `deliver({ reaction })` (a reaction on the bot's previous message) rather than the header.

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

### Tangent focus via a hidden `custom_message`, injected at checkpoint-set (issue-411)

**Choice**: Whenever a checkpoint is set (manual `/checkpoint`, automatic `set-checkpoint`, the system-origin side-task checkpoint, or rollback Case B), `setCheckpointAndFocus` sets the checkpoint and appends a hidden `custom_message` (`display:false`, in LLM context) telling the assistant to give the side task full, unhurried focus with no pressure to return to the parked main line. It reuses `appendInContextEntry` — the same primitive `injectRelatedBranchContext` uses for the related-branch pointer — not a `provideContext` system-prompt append; the single `setCheckpointAndFocus` entry point is called from every checkpoint-set site so the focus injection cannot be forgotten at a new one.
**Why**: A tangent runs off the parked main line, whose turns stay visible in the branch, so the assistant tends to rush the side task to get back to them. A focus instruction present for every tangent turn counters that. `provideContext` caches its result once per session (`src/agent/system-prompt-section.ts`) and so cannot reflect a checkpoint set mid-session, and a `before_agent_start` system-prompt override would also bind the classifier's shadow-fork; a transcript entry is fork-invariant, restart-safe, and self-cleaning. Because the entry is type `custom_message` (not `message`), `checkpointHasTangent` does not count it (the empty-tangent guard stays correct) and `renderBranchTranscript` does not render it (the tangent summary stays clean), and `collapseTangent` parks it with the tangent when the checkpoint is summarized.
**Alternatives Considered**: A per-turn system-prompt append via `provideContext` (rejected — per-session caching can't react to a mid-session checkpoint, and fork-binding risks biasing the classifier); reinforcing every tangent turn (unnecessary — one entry at checkpoint-set precedes and rides all tangent turns).
**Consequences**: Pro — proven injection pattern; no new persistence surface; self-cleaning. Con — one extra hidden entry per checkpoint (negligible; parked with the tangent).

### System-origin side tasks checkpointed, not absorbed (issue-411)

**Choice**: A system-origin message (`origin: "system"`, `boundary: "skip"`) that begins a new interactive turn — a queue digest, a fired session task — parks the main line via an automatic checkpoint when a main line is parkable and no checkpoint is active, instead of appending to the main branch. It is gated on `autoSetCheckpoint` (the same kill-switch as the classifier's auto `set-checkpoint`) and excluded for replays via a `metadata.replay` marker the coordinator stamps, and it is not a `/rollback` target.
**Why**: Such messages bypassed classification (R15) and were absorbed into the main branch, diluting it and rushing the side task. Parking is the right semantics — the main line is held, the side task runs as a tangent, and `/back` or `summarize-to-checkpoint` returns. `replay()` shares the `origin: "system", boundary: "skip"` shape but already carries rollback's framing (a checkpoint in Case B, a topic in Case A), so the `replay` marker excludes it. System turns are not user messages, so `/rollback`'s immediacy counter would not treat them as the triggering turn — recovery is `/back` instead.
**Alternatives Considered**: Running the classifier on system-origin messages (rejected — replay would re-classify and loop, and a task firing shouldn't shift the user's topic); making it a `/rollback` target (rejected — user-message immediacy counting doesn't fit system turns).
**Consequences**: Pro — system side tasks no longer dilute the main line; bounded by the kill-switch and `/back`. Con — a system task that is actually about the current topic gets parked (recoverable via `/back`, and strictly better than the old absorb-and-rush).

## System Behavior

### Scenario: Topic shift mid-conversation

**Given**: An active trunk whose live branch has an assistant turn
**When**: The classifier returns `shift`
**Then**: The branch is summarized and collapsed into a `branch_summary` (`kind: "topic"`), the leaf re-seats onto the summary, the summary becomes the new base, and a "🆕 New topic" header (advertising `/rollback`) is set on the response. If a prior branch clearly matches the new message, a pointer to it is injected. A `session:topic-changed` event (`reason: "auto-shift"`) is emitted so downstream consumers reset per-branch state. A manual `/new` takes the same path (same summary, same related-branch injection, same event with `reason: "/new"`) except it records no rollback target; a forced jump to an earlier branch emits `reason: "earlier-branch"`.

### Scenario: A side tangent, parked and returned

**Given**: An active trunk at a main-line tip `C`
**When**: The user sends `/checkpoint`, exchanges a tangent, then `/back`
**Then**: `C` is recorded as `checkpointId`; on `/back` the tangent turns are folded via `collapseTangent(C, …)`, the leaf re-seats onto the summary rooted at `C`, `checkpointId` clears, and the main line resumes at `C` — never collapsed. A "↩️ Summarized to checkpoint" ack is shown.

### Scenario: Auto set-checkpoint then summarize-to-checkpoint

**Given**: A checkpoint is not active and the classifier detects a self-contained side task beginning
**When**: It returns `set-checkpoint`
**Then**: A checkpoint is set, `lastAutoDecision` records it, the tangent-focus instruction is injected, a "📌 Checkpoint set" header (advertising `/rollback`) is set, and the message streams as the first tangent turn. Later, with the checkpoint active, `summarize-to-checkpoint` folds the tangent and resumes the main line.

### Scenario: A system-origin side task, parked and focused

**Given**: An active main conversation (an assistant turn since the base) and no checkpoint active
**When**: A system-origin side task arrives (a queue digest or fired session task, `origin: "system"`, `boundary: "skip"`, not a replay)
**Then**: The main line is parked via an automatic checkpoint, the tangent-focus instruction is injected, an informational "📌 Checkpoint set" header (no `/rollback`) rides the response, and the side task streams as the tangent's first turn with full focus — it is never classified, and never absorbed into the main branch. `/back` or a later `summarize-to-checkpoint` folds it away and resumes the main line. A rollback replay (`metadata.replay: true`) is excluded — its framing is already applied.

### Scenario: A short unrelated interruption, checkpointed and resumed

**Given**: An active evening-check-in workflow (visible in the conversation history) and no checkpoint active
**When**: The user asks to log a fuel expense to their wallet and the classifier returns `set-checkpoint`
**Then**: A checkpoint is set at the workflow tip and the expense request streams as the first side-task turn; after the wallet entry completes (2-3 turns), a later `summarize-to-checkpoint` folds it away and the evening check-in resumes — the workflow was parked, never collapsed.

### Scenario: A quick capture after the topic wrapped up

**Given**: An active trunk whose current topic has just reached a natural finish (the last exchange wrapped it up — the work was completed and acknowledged)
**When**: The user sends a small, unrelated capture (e.g. "log my lunch") and the classifier judges the current topic finished
**Then**: The classifier returns `shift`, not `set-checkpoint` — a finished topic has no main line to park and resume, so the capture starts a fresh topic. The same capture mid-workflow (an open topic) would instead `set-checkpoint`, parking the main line to resume later.

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
- `set-checkpoint` fires for a self-contained side task **distinct from** the current thread — the discriminator from `continue` is that the message clearly starts a separate task rather than following on (a note/reminder capture, a logging request, a lookup, a background task or daily ceremony), and the discriminator from `shift` is substantiveness (a minor interleaved side task to fold away vs. a new main topic); it is not gated on a turn count. The classifier infers the mid-workflow/mid-conversation state from the conversation history the shadow fork already reads; no workflow flag is injected. The set-checkpoint-vs-`shift` discriminator also weighs whether the current topic has reached a natural finish (issue-423): `set-checkpoint` presupposes an open main line to park and resume, so it is gated on an open topic — once the current topic is wrapped up, an unrelated message shifts (even a small capture), since there is no main line to park. Finish-state is a semantic judgment the model infers from the conversation history the shadow fork already reads, so it needs no injected signal (unlike `checkpointActive`, which is injected because the active checkpoint lives in fork-opaque boomerang-state). Broadening the criterion (issue-411) addresses under-triggering for clear side tasks; ambiguity still falls back to `continue`, and the `autoSetCheckpoint` kill-switch bounds over-triggering.
- `summarize-to-checkpoint` recognizes a return to the main line from explicit "going back" phrasing, from the message's topic matching the pre-checkpoint conversation, or (the common case) from the side task looking done and the message not following on from it but fitting the main line as its next step, even without naming it (issue-419). The discriminator from `shift` is that a return resumes an existing conversation rather than starting a new one — a message orphaned from the side task that does not fit the main line is a new topic, so it is a `shift`, not a summarize; detection quality is empirical, so the prompt stays conservative (genuinely unsure ⇒ `continue`).
- The checkpoint model replaced an earlier "parked sub-session" idea during speccing — checkpoints are strictly simpler (linear, reuse `branchWithSummary`/`branch`, no sub-session juggling).
- The append-only/no-delete property is load-bearing and pi-upgrade-sensitive; it is captured in `docs/reference/pi-sdk-notes.md` and re-verified on each upgrade (per ADR-014). Rollback relies on `branch(id)` re-seat and the absence of a delete primitive.
- The turn-scoped decision header is a general channel-rendering capability owned by the [conversation loop](./conversation-loop.md) and rendered by the [Telegram channel](./telegram.md); the boundary middleware only produces the descriptor — rendered as an italic header, or as a `reaction` emoji on the bot's own message when the descriptor carries one.
