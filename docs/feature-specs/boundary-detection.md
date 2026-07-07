# Boundary Detection

<!-- This spec describes the current system capability. Updated through delta reconciliation. -->

## Overview

Boundary detection owns the **topical** boundary of the daily trunk: for every idle user message it decides whether the conversation continues the current topic branch, shifts to a fresh one, parks a temporary side tangent against a checkpoint, or folds a tangent back to the main line. Topic shifts and tangent folds collapse a branch into a `branch_summary` entry on the single daily session tree (the "trunk") and re-seat the live leaf onto the summary — the abandoned branch survives in the append-only tree, reachable later. An `ask_branch` tool recovers focused context from any prior topic branch's full conversation.

The feature ships as the `boundary` extension (`src/extensions/boundary/`): an inbound middleware that classifies each message and drives branch transitions on the live trunk, plus the `ask_branch` agent tool. The daily-trunk model ([ADR-014](../architecture/ADR-014-session-source-of-truth.md)) makes the session a single append-only tree for the day: there is no idle boundary, per-session rolling summary, or cross-session resume — a topic shift is a branch collapse, not a new session. A trunk closes on a nightly cron, on shutdown, or lazily on a stale day (see [conversation-loop](conversation-loop.md)). Classification runs on a detached shadow fork (it never mutates the live session) and fails open.

Side conversations are realized as **checkpoints**: a passive marker at the main-line tip that lets the conversation take an inline tangent and later summarize it back to a collapsed branch rooted at the checkpoint, returning the leaf to the main line intact. The main line is **parked, never collapsed** — the defining difference from a topic shift. Checkpoints and the summarize-to-checkpoint return are each available manually (`/checkpoint`, `/back`) and automatically (two classifier results). A `/rollback` command reverses the most-recent *automatic* checkpoint/topic decision.

## User Stories

- As a user, I want topic shifts detected so unrelated conversations become separate branches on the day's trunk and context doesn't bleed between them
- As a user, I want to take a quick side tangent and then return to the main thread with its context intact, like a scratch buffer, so a tangent doesn't force a new topic or collapse the work in progress
- As a user mid-workflow, I want a quick unrelated side request (e.g. logging an expense) handled inline and folded away, then to resume my workflow where I left off — without it starting a new topic or losing my place
- As a user, I want to reverse a wrong automatic topic/checkpoint decision and have my message re-answered under the right framing
- As the agent, I want to recover missing context from a previous topic branch's full conversation when a summary doesn't carry the detail
- As a developer, I want boundary detection to be best-effort so classification failures never block message handling

## Requirements

| ID | Requirement |
|----|-------------|
| R0 | Each idle user message is classified as `continue`, `shift`, `set-checkpoint`, or `summarize-to-checkpoint` via a single side-channel classification on a detached shadow fork (`classifier` model tier); the live session is never mutated by the classifier |
| R1 | Classification is skipped (the message continues) when the current branch has no completed assistant turn since its base (the empty-branch guard) — two shifts in immediate succession cannot collapse an empty branch |
| R2 | A `shift` decision collapses the current branch into a `branch_summary` entry on the trunk (unless the empty-branch guard skips it), re-seats the live leaf onto the summary, and sets the summary as the new base; the abandoned branch survives in the append-only tree, reachable via the `originalLeafId` recorded in the entry's details |
| R3 | After a successful topic change (`shift` or manual `/new`), an LLM matcher compares the new message against prior topic-branch summaries; on a clear match it injects a single hidden pointer (the matched branch's last exchange and an `ask_branch` hint) into the new branch's context — branches are never merged, and a failed or ambiguous match injects nothing |
| R4 | `continue` (the default) appends the message to the current branch with no transition |
| R5 | Classifier errors and unparseable output fail open: the decision defaults to `continue` and the message proceeds in the current branch |
| R6 | `set-checkpoint` parks the main line: a checkpoint is recorded at the current main-line tip (in the trunk's boomerang-state), the message continues inline as the tangent's first turn, and the decision is surfaced. Only one checkpoint is active at a time; a new one overrides; it persists across restart (it lives on the session file) and is cleared when its tangent is summarized away. It is emitted only when no checkpoint is already active |
| R7 | `summarize-to-checkpoint` folds the tangent taken since the active checkpoint into a `branch_summary` rooted at the checkpoint, re-seats the leaf onto the summary (the main line resumes at the checkpoint), clears the checkpoint, and surfaces the decision. It is recognized when the message clearly returns to the main line — either it explicitly references going back to / resuming what was discussed before the side task, or its topic clearly matches the main-line topic (the classifier reads the pre-checkpoint conversation from the history) — and emitted only when a checkpoint is active that has at least one conversational turn after it (the empty-tangent guard). A return to the main line is not a new topic, so it is never a `shift` |
| R8 | Both the one-turn tangent (checkpoint + a single exchange + summarize) and the multi-turn side conversation share one checkpoint mechanism — summarize reuses the topic collapse primitive with the checkpoint as the branch point, marked `kind: "tangent"` |
| R9 | A summarized tangent is parked away: it is excluded from the topic-branch enumeration, from `ask_branch` targets, and from trunk-close memory extraction — only its summary persists in the main line. Tangent ids (`tangent-N`) count on their own sequence so topic ids (`topic-N`) stay clean |
| R10 | Manual controls mirror the automatic ones. `/checkpoint` sets a checkpoint at the tip (idempotent at the tip — setting the same tip twice is a no-op with a notice); `/back` summarizes the tangent since the checkpoint back into it (no checkpoint → notice; no tangent → notice and no summary created). Manual commands mark the message handled and acknowledge immediately; their decision label rides the ack, not a streamed header |
| R11 | A checkpoint clears on a topic shift (manual `/new` or auto `shift`): the main-line point it marked is gone, and a notice is surfaced |
| R12 | `/rollback` reverses the most-recent *automatic* `set-checkpoint` or `shift` decision in exactly two cases — by re-seating the leaf to the pre-decision tip, applying the opposite transition, and replaying the triggering message: (a) `set-checkpoint` → collapse the restored branch as a topic and replay the message as the new topic's first turn; (b) `shift` → set a checkpoint at the restored tip, mark the auto-shift's orphaned summary `reversed`, and replay the message as the tangent's first turn. It is a no-op in every other case (no recent auto decision; a manual decision; a `continue` or `summarize-to-checkpoint`; or any exchange since the decision). The decision state lives on the session tree, so a restart between the decision and `/rollback` (with no exchange since) still rolls back. The wrong-framing exchange goes off-path (append-only — not deleted, not re-extracted) |
| R13 | Branching decisions — topic shift, set-checkpoint, summarize-to-checkpoint, rollback — are surfaced as a turn-scoped header on the decision's response (a short label and one-line note) that the streaming renderer does not overwrite and that is not carried to later turns. Only the automatic `set-checkpoint` and `shift` decisions advertise `/rollback`; the header for a manual decision or a `summarize-to-checkpoint`/`rollback` is informational |
| R14 | The `ask_branch` tool answers a focused question from a prior topic branch's **full** conversation (headless-forking the branch's abandoned leaf), not its summary. It resolves the branch from the topic-branch enumeration (so tangents and reversed summaries are never targets) and reports gracefully for an unknown id or an unavailable branch |
| R15 | A message with `metadata.boundary === "skip"` (system-originated injections) bypasses all boundary logic. A `/new `-prefixed (`forceNew`) message forces a topic shift even when detection is off. A `metadata.forcedBranchId` (a Telegram reply/reaction/button resolving a referenced message to its branch) force-routes: same live branch → append; an earlier recorded branch → forced collapse + related-context injection; an unknown id → fall through to detection |
| R16 | `[extensions.boundary] enabled = false` disables topic-shift detection (the classifier is not run); manual `/checkpoint`, `/back`, `/rollback`, forced `/new`, and forced-branch references still work. Two per-result kill-switches — `autoSetCheckpoint` and `autoSummarizeToCheckpoint` (both default `true`) — disable each automatic checkpoint result independently without losing the manual commands or topic detection; a suppressed result degrades to `continue` |
| R17 | Graceful edge behavior: summarize with no active checkpoint or no tangent; rollback with no recent auto decision or a stale/consumed one; classifier failure (fail open); and concurrent manual and automatic checkpoint coincide as one active checkpoint (idempotent at the tip) |
| R18 | A genuine topic change emits a `session:topic-changed` event (defined in `src/events.ts`, payload `{ reason: "auto-shift" \| "/new" \| "earlier-branch" }`) so downstream consumers reset per-branch state — e.g. the skills extension clears its proactive-injection record so the new branch re-evaluates which skills are relevant. It fires on an auto-detected `shift`, a manual `/new`, and a forced jump to an earlier branch, but never on `continue`, `set-checkpoint`, or `summarize-to-checkpoint`. The auto `shift` and manual `/new` share one collapse routine (same summary, same status, same related-branch pointer injection); only the auto `shift` records a `/rollback` target (R12), and a manual `/new` is not a rollback target |

## Behaviors

### Topic Classification (R0, R1, R4, R5)

The inbound middleware (`src/extensions/boundary/index.ts`) calls `classifyShift` (`src/extensions/boundary/classifier.ts`), which forks the live branch into a throwaway headless session on the `classifier` tier, asks it which decision fits the incoming message, and returns one of `continue` / `shift` / `set-checkpoint` / `summarize-to-checkpoint`. The middleware injects whether a checkpoint is active so the classifier only offers the checkpoint decision valid for the current state. The `set-checkpoint` result targets a short, self-contained, **unrelated** side request the user makes while mid-workflow or mid-conversation (the classifier reads that context from the conversation history): the main line is parked and resumes once the side task is done, rather than shifting to a fresh topic. The `summarize-to-checkpoint` result fires when the message clearly returns to the main line — either it explicitly references going back to / resuming what was discussed before the side task, or its topic clearly matches the main-line topic (read from the pre-checkpoint conversation the shadow fork already sees); a return to the main line is not a new topic, so it is never a `shift`. `shift` is reserved for a substantive new topic, and ambiguity conservatively falls back to `continue` (or `shift`).

**Acceptance Criteria**:
- Given a branch with a completed assistant turn and an incoming message, when the middleware runs, then the classifier decides among the four results using the live system prompt and the incoming message
- Given a branch with no assistant turn since its base, when classification would run, then it is skipped entirely (no fork) and the message continues — the empty-branch guard prevents collapsing a branch too small to summarize
- Given the classifier returns an unrecognized value or unparseable output, when parsed, then the decision falls back to `continue` and the error is logged
- Given the classifier call throws, when caught, then the decision falls back to `continue`, the error is logged, and the message proceeds in the current branch

### Topic Shift (R2, R3)

On `shift`, the current branch is summarized and collapsed; a related prior branch's context may be pulled onto the fresh branch.

**Acceptance Criteria**:
- Given a `shift` decision on a branch with an assistant turn, when the middleware acts, then the branch is summarized via a side completion, collapsed into a `branch_summary` (carrying `kind: "topic"`, the abandoned leaf id, the prior base, and the next `topic-N` id), the leaf is re-seated onto the summary, and the summary becomes the new base
- Given a `shift` decision on a branch with no assistant turn, when the middleware acts, then no summary is created and the new branch starts directly (the empty-branch guard)
- Given a successful auto shift and a prior branch whose summary clearly matches the new message, when the matcher runs, then a single hidden pointer to that branch (its last exchange and an `ask_branch` hint) is injected into the new branch; the matched branch is never merged
- Given the matcher returns no clear match (ambiguous message, genuinely new topic, or a match error), when it runs, then nothing is injected

### Checkpoints (R6, R8, R11)

A checkpoint is the main-line tip marker that lets a tangent run inline and later be summarized back.

**Acceptance Criteria**:
- Given an active trunk at a main-line tip, when the user sends `/checkpoint`, then the current tip is recorded as the active checkpoint (overriding any prior) and a confirmation is surfaced
- Given an active checkpoint already at the current tip with no tangent taken, when `/checkpoint` is sent again, then it is a no-op with a notice ("Checkpoint already at this tip")
- Given an active checkpoint, when the trunk reopens after a restart, then the checkpoint is restored from the session file
- Given an active checkpoint, when a topic shift occurs (manual `/new` or auto `shift`), then the checkpoint is cleared (the main-line point it marked is gone) and a notice is surfaced
- Given a checkpoint that is never summarized, when the conversation continues, then nothing breaks — the checkpoint is simply an unused marker

### Summarize-to-checkpoint (R7, R8, R9)

`/back` (manual) or `summarize-to-checkpoint` (auto) folds the tangent since the checkpoint back into the main line.

**Acceptance Criteria**:
- Given an active checkpoint with one or more tangent turns after it, when `/back` (or `summarize-to-checkpoint`) runs, then the tangent turns are summarized, collapsed into a `branch_summary` rooted at the checkpoint (marked `kind: "tangent"`, with a `tangent-N` id), the leaf re-seats onto that summary, the checkpoint clears, and the main line resumes at the checkpoint
- Given a one-turn tangent (a single exchange after the checkpoint), when it is summarized, then the behavior is identical to the multi-turn case — one mechanism, no special-casing
- Given a checkpoint with no conversational turn after it (the empty-tangent guard), when `/back` runs, then it is a no-op with a notice ("No tangent to summarize") and no summary is created
- Given no active checkpoint, when `/back` runs, then it is a no-op with a notice ("No checkpoint to summarize to")
- Given a tangent that has been summarized, when branch records are enumerated or `ask_branch` is directed at it, then it is excluded — tangents are parked away; only the summary persists in the main line
- Given a summarized tangent, when its kind is computed, then it reads as `"tangent"` and counts on the `tangent-N` sequence, leaving `topic-N` ids for topics only

### Rollback (R12)

`/rollback` reverses the single most-recent qualifying automatic decision by rewinding and replaying.

**Acceptance Criteria**:
- Given the most-recent automatic decision was `set-checkpoint` and exactly one user-message turn (the triggering message) has occurred since, when `/rollback` runs, then the leaf is re-seated to the pre-decision tip, the restored branch is collapsed as a topic summary, and the triggering message is replayed as the new topic's first turn (re-answered)
- Given the most-recent automatic decision was `shift` and exactly one user-message turn has occurred since, when `/rollback` runs, then the leaf is re-seated to the pre-decision tip, a checkpoint is set there, the auto-shift's orphaned topic summary is marked `reversed`, and the triggering message is replayed as the first tangent turn
- Given a `reversed` summary, when branch records are enumerated or extraction runs, then it is excluded — a reversed decision leaves no memory or query residue
- Given the wrong-framing exchange that `/rollback` moved off-path, when the trunk later closes, then that exchange is not re-extracted (it is an off-path dead branch, not a deletion)
- Given the most-recent decision was manual, was `continue` or `summarize-to-checkpoint`, or there is no recent automatic decision — or more than one exchange has occurred since it, or the decision marker can no longer be resolved — when `/rollback` runs, then it is a no-op with a notice ("Nothing to roll back")
- Given the process restarted between an automatic decision and `/rollback` with no exchange in between, when the trunk resumes and `/rollback` runs, then the rollback still works (the decision state lives on the session tree)

### Decision Surfacing (R13)

Every branching decision is surfaced as a turn-scoped header on its response.

**Acceptance Criteria**:
- Given an automatic `set-checkpoint` or `shift` decision, when its response renders, then a persistent header (e.g. "📌 Checkpoint set", "🆕 New topic") is attached that the streamed text does not overwrite, and it signals `/rollback` is available
- Given a `summarize-to-checkpoint`, `rollback`, or manual decision, when its response renders, then the header is informational (no `/rollback` advertised)
- Given a decision header was shown on a response, when the next turn renders with no new decision, then no header is carried forward — it is turn-scoped

### ask_branch Tool (R14)

`ask_branch` recovers context from a prior topic branch's full conversation.

**Acceptance Criteria**:
- Given a focused question and a known topic-branch id, when `ask_branch` runs, then the branch's full conversation (its abandoned leaf) is headless-forked and the question is answered from it; the throwaway fork file is cleaned up after
- Given a tangent or `reversed` branch id, when `ask_branch` resolves it, then it is not a target (those records are excluded from the enumeration) and an unknown id reports gracefully
- Given an unknown branch id or a branch whose file cannot be opened, when `ask_branch` runs, then it reports gracefully without failing the turn

### Metadata Fast-Paths and Forced Routing (R15)

The middleware checks metadata before classification. Order: `boundary === "skip"`, then the manual commands, then `forcedBranchId`, then `forceNew`, then (when enabled) classification.

**Acceptance Criteria**:
- Given a message with `metadata.boundary === "skip"`, when the middleware runs, then it calls `next()` immediately — no command, collapse, or classification
- Given a message with `metadata.forceNew === true`, when the middleware runs, then the live branch is collapsed (subject to the empty-branch guard) and the message proceeds as a fresh topic — even when detection is disabled
- Given a `metadata.forcedBranchId` naming the live branch, when the middleware runs, then the message appends to the current branch with no transition
- Given a `metadata.forcedBranchId` naming an earlier recorded branch, when the middleware runs, then the live branch is collapsed and a pointer to the referenced branch is injected before the message proceeds
- Given a `metadata.forcedBranchId` that resolves to no record (e.g. a stale routing row), when the middleware runs, then it falls through to classification rather than silently appending

### Configuration (R16)

**Acceptance Criteria**:
- Given `[extensions.boundary] enabled = false`, when the extension loads, then setup logs the disabled state and the classifier is not run; manual `/checkpoint`, `/back`, `/rollback`, forced `/new`, and forced-branch references still work
- Given `autoSetCheckpoint = false`, when the classifier returns `set-checkpoint`, then the result is suppressed (the message continues) while manual `/checkpoint` still works
- Given `autoSummarizeToCheckpoint = false`, when the classifier returns `summarize-to-checkpoint`, then the result is suppressed (the message continues) while manual `/back` still works
