# Skill Evolution

<!-- This spec describes the current system capability. Updated through delta reconciliation. -->

## Overview

The `skill-evolution` extension (`src/extensions/skill-evolution/`) makes workspace skills self-improving. At each trunk close it runs as a `main`-phase post-processor concurrently with memory post-processing, reading the day's closed topic branches for evidence of skill friction — skills invoked that failed or were misapplied, and workarounds the agent resorted to — and accumulating that evidence as pattern pages in a wiki-style store under `memories/skill-evolution/`. A separate proposal agent turns accumulated evidence into one pushed branch per proposed skill modification (an edit to an existing workspace skill or a new `SKILL.md`); live skills are never edited automatically — every proposal is a reviewable branch the user merges or discards. Each run first reconciles the previous run's proposals from fresh remote state (accepted / rejected / still pending) and records every proposal with its outcome in a host-written skill-impact log, so rejected suggestions are never re-proposed. When a run creates at least one verified proposal it dispatches an ad-hoc background task seeded with the run's context (the configured post-work prompt, or a default notification-only one). The whole run is fail-soft — a failure never blocks trunk close — and requires a workspace `origin` remote (none configured: the run is skipped entirely). The feature is enabled by default with a config toggle; proposals target workspace skills only, never built-in ones. The proposal agent authors its changes grounded in the built-in authoring guides (`skill-authoring`, `workflow-authoring`), which ride its isolated run as reference skills.

## User Stories

- As a user, I want my assistant's skills to improve themselves from evidence of how they performed each day, so that gaps and friction get fixed without me having to notice and request every change
- As a user, I want every proposed skill change delivered as a reviewable branch — never applied silently — so that nothing steers the agent's behavior without my review
- As a user, I want previously rejected suggestions never re-proposed, so the proposals stay worth reading
- As a user, I want yesterday's proposals reconciled from their remote branches (merged / still open / discarded), so the impact log reflects what actually happened

## Requirements

| ID | Requirement |
|----|-------------|
| R0 | The day's completed conversations are analyzed for skill usage, and recurring problems become reviewable, branch-based skill-modification proposals — live skills are never edited silently |
| R1 | Each run first reconciles prior proposals from remote state after a fetch — a failed fetch (or a default-branch resolution failure) aborts the run softly before any analysis. Classification is reachability-first: recorded tip reachable from the remote default branch → accepted (whether or not the branch still exists); otherwise branch still present → pending (stays `proposed`); otherwise → rejected. The log is updated before new analysis, and rejected proposals are never re-proposed |
| R2 | Analysis runs at trunk close, concurrently with [memory](memory.md) post-processing, reusing the same topic-branch walk through shared logic (`walkBranches` in `src/sessions/branch-walk.ts`) — no duplicated walk |
| R3 | Per-branch data points are collected: which skills were invoked, which failed or were misapplied, and what workarounds the agent resorted to |
| R4 | Data points persist under `memories/skill-evolution/` wiki-style: one-line index entries (PROBLEM + ROOT CAUSE + FIX) and per-pattern pages updated with new evidence instead of duplicated |
| R5 | A skill-impact log records every proposal with its outcome (proposed → accepted / rejected / still pending) |
| R6 | Pattern dedup and size caps are agent-enforced: update-not-duplicate conventions with per-pattern pages kept under ~50 lines (the memory-store convention) |
| R6b | An in-run maintenance pass consolidates near-duplicate patterns and sweeps emptied files |
| R7 | Proposals cover edits to existing workspace skills and creation of new skills |
| R8 | Branch-per-edit: a proposal agent authors each proposal on its own branch via a temporary worktree (edit → commit → push to origin); the worktree and local branch are removed afterwards, and the live working tree is never touched. Each proposal is logged only after the host confirms from git state that the pushed branch exists (tip SHA read from git) — the proposal agent's report alone never creates or confirms a log entry |
| R9 | Branches are named `skill-evolution/<skill>-<slug>` and unique in the `skill-evolution/*` namespace on origin — a push over any existing remote name in the namespace is refused and the agent picks a suffixed name, so open proposals never share a branch name |
| R10 | When a run creates at least one verified proposal, it dispatches an ad-hoc background task (created by the [tasks](tasks.md) extension) seeded with the run's context; the prompt is the configured post-work prompt, or a default prompt that only generates a notification. The dispatch payload carries an explicit `goal` naming the created proposals (so the background runner skips goal extraction) |
| R11 | Fail-soft: a failed run never blocks trunk close — it logs, warns (a warning notification), and the day is skipped. Completed per-branch analyses persist via markers; unanalyzed branches of a failed day are abandoned (the trunk retires) and never re-analyzed |
| R12 | Analysis is idempotent per branch: at most once per branch, and a re-run close skips already-analyzed branches |
| R13 | The feature is enabled by default and toggleable via `[extensions.skill-evolution] enabled`; an optional `postWorkPrompt` configures the post-proposal background prompt (R10) |
| R14 | Proposals target workspace skills only; built-in bundled skills are out of scope as *proposal targets* — the bundled authoring guides are loaded into the proposal run as reference skills (R17), never edited |
| R15 | The feature requires a workspace `origin` remote — when none is configured, the run is skipped entirely (logged; no analysis, no run-time store writes; the bootstrap layout seeding is unaffected) |
| R16 | The extension owns its agent-facing guidance rather than baking it into the core base prompt: a `skill-evolution-usage` context section (`src/extensions/skill-evolution/usage.ts`, contributed via `provideContext(..., "skill-evolution-usage")`, scoped to `["main"]` — background runs have no branches to analyze and no say in review) tells the agent what the pass does (accumulates friction as pattern pages under `memories/skill-evolution/`, turns recurring patterns into pushed proposal branches) and the one rule that matters conversationally: proposals are reviewable branches the user merges — the pass never edits live skills silently. A `references/skill-evolution.md` file carries the stage-by-stage detail ([DES-014](../design/DES-014-two-tier-agent-facing-documentation.md)) |
| R17 | Authored proposals follow the house authoring conventions: the proposal run's skills are exactly the two built-in authoring guides (`skill-authoring`, `workflow-authoring`) — discovered from the bundled directory via the agent layer's `skillPaths` option and force-loaded into the run's input via `forceLoadSkills` — and the proposal system prompt requires every file the agent creates or edits under the proposal worktree's `skills/` to conform to them (new skills follow the full `SKILL.md` conventions; new workflows use the step-directory format; edits preserve established structure). The rule is conditional on the guides' content being present in the run's input, so fail-soft loading and the policy never disagree; the guides are reference material, never proposal targets |

## Behaviors

### Reconciliation (R1, R15)

**Acceptance Criteria**:
- Given a previous run logged proposals as `proposed`, when the next run starts, then it fetches from origin and classifies each before any analysis: recorded tip reachable from the remote default branch → accepted (whether or not the branch still exists); otherwise branch still present → pending; otherwise → rejected
- Given a proposal merged via a squash merge, when it is classified, then it records as rejected (documented limitation: the change has landed, and rejected also blocks re-proposal)
- Given a fetch failure (or a default-branch resolution failure) during reconciliation, when the run starts, then the run aborts softly (warning logged and notified, nothing classified or analyzed) rather than analyzing against stale or unresolved refs — classifying against an unresolved default branch would mass-reject every open proposal irreversibly
- Given the first run on a workspace (empty impact log), when it starts, then reconciliation is a no-op, the bootstrap-seeded store is used, and analysis proceeds
- Given a workspace with no `origin` remote, when the trunk closes, then the skill-evolution run is skipped entirely — no analysis forks, no run-time store writes, no log changes — and the skip is logged

### Analysis Walk (R2, R3, R12)

**Acceptance Criteria**:
- Given a trunk closes with topic branches, when the processor runs, then each unanalyzed topic branch is analyzed with that branch's conversation live in the analyzing agent's context — using the same topic-branch enumeration memory post-processing uses (tangents and reversed branches excluded), via the shared walk logic
- Given memory post-processing is running, when the skill-evolution processor runs, then both complete correctly and neither's writes corrupt the other's (the two run concurrently by design, on disjoint stores)
- Given a branch already carrying an analysis marker (e.g. the close re-ran after a memory failure), when the walk runs, then that branch is skipped
- Given a background/headless close with no trunk, when the processor runs, then it is a no-op
- Given a branch with no skill-usage signal, when analyzed, then the fork may record nothing for it (a clean no-op, not an error; the marker is still written)
- Given a branch whose analysis throws, when the walk runs, then that branch is isolated and logged, and the day continues with the remaining branches
- Given a lifecycle close, when the processor runs, then it surfaces friendly progress on the channel's preparation lead-in (`statusLabel: "Evolving skills"`) and per-branch lines ("Analyzing skills — branch i/n…") — a headless run with no status surface emits nothing

### Data Store (R4, R5, R6, R6b)

**Acceptance Criteria**:
- Given data points identifying a recurring pattern (e.g. a CLI flag used in practice but missing from a skill's guidance), when the analysis fork persists them, then the pattern's page is updated with the new evidence — not duplicated — and the index carries a one-line entry in the PROBLEM + ROOT CAUSE + FIX form
- Given patterns accumulating across nights, when the maintenance pass executes, then near-duplicate patterns are merged, pages stay under ~50 lines, and emptied files are swept
- Given a proposal is created and verified, when it is recorded, then the skill-impact log entry captures the skill, pattern link, branch name, tip SHA, date, one-line description, and status `proposed`
- Given a malformed impact-log entry or a missing pattern page (user-edited store), when reconciliation or the proposal agent reads the store, then the entry is logged and skipped — never fatal

### Proposals and Branches (R7, R8, R9, R14, R17)

**Acceptance Criteria**:
- Given accumulated evidence of a recurring skill problem with no impact-log entry for it, when the proposal agent runs, then it proposes at most one change for the pattern — an edit to the existing skill, or a new `SKILL.md` for a recurring workflow that has no skill — and may decline a pattern that does not justify a change (an empty report is valid)
- Given a run where no pattern is eligible (every pattern has an impact-log entry) or the store has no pattern pages, when the propose stage is reached, then no proposal agent is invoked (no LLM call) and the namespace sweep still runs — clearing any worktrees or local branches a crashed run left behind
- Given the proposal agent is seeded, then its input carries the impact log and excludes patterns with any impact-log entry (`proposed`, `accepted`, or `rejected`) from proposal candidates (the never-re-proposed rule is enforced input-side), and the run's skills are exactly the two built-in authoring guides (`skill-authoring`, `workflow-authoring`) — discovered from the bundled directory and force-loaded into the run's input as hidden reference content (R17)
- Given proposals are created, then each is authored on its own branch in a temporary worktree under the OS temp dir (never inside the workspace repo), committed, and pushed to origin; the worktree and the local branch are removed afterwards; the main working tree and current branch are untouched throughout
- Given proposals, when branches are named, then they follow `skill-evolution/<skill>-<slug>` and are unique in the `skill-evolution/*` namespace on origin — a push is refused for any name outside the pattern, the default branch, or one already present on the remote (the agent retries with a suffixed name), so pushes are create-only, never forced
- Given the proposal agent is running, when it modifies files, then it may only touch files under `{workspace}/skills/` — built-in skills are never touched (host-verified from the pushed diff)
- Given the proposal agent completes, when its report is processed, then each logged proposal is verified from git state (the remote branch exists; the tip SHA is read from git) before the log entry is written — the agent's report alone never creates a log entry
- Given a push is denied by the remote mid-proposal (read-only remote, missing credentials), then already-pushed proposals are still verified and logged, the denied one's report is dropped at verification, no stray worktrees or local branches survive cleanup, and the run settles without blocking the close
- Given the proposal agent fails mid-run for any other reason, then the same guarantees hold: whatever pushed and verifies is logged, no stray worktrees or local branches, soft failure

### Reporting (R10)

**Acceptance Criteria**:
- Given a run that created at least one verified proposal, when it completes, then an ad-hoc background task is dispatched (via the `task:dispatch-background` app event) with the configured post-work prompt — or the default notification-only prompt when none is configured — and a `goal` naming the created proposals (so the background runner skips goal extraction), seeded with the run's context (proposals created, patterns touched, log path)
- Given a run that created no verified proposals, then no background task is dispatched
- Given no post-work prompt is configured, when the background task runs, then the background agent only generates a notification and takes no further action
- Given a partially-failed proposal phase that still verified at least one proposal, then the reporter still dispatches

### Failure, Idempotency, and Configuration (R11, R12, R13)

**Acceptance Criteria**:
- Given any failure inside the skill-evolution run, when the trunk closes, then memory post-processing and trunk retirement proceed unaffected; the failure is logged and a warning notification is emitted
- Given a failure after some branches were analyzed, then those branches' markers and store writes persist, the unanalyzed branches are abandoned for good, and the day yields no proposals from the abandoned branches
- Given `[extensions.skill-evolution] enabled = false`, then nothing registers; given the default configuration, then the feature is active

## Notes

Documented limitations (deliberate out-of-scope):

- Background-task transcripts are not analyzed — only conversational topic branches
- Built-in bundled skills are never edited; proposals touch `{workspace}/skills/` only
- Proposals are never auto-merged; the user merges or discards each branch. Skill retirement/deletion is not proposed
- Squash-merged proposals record as rejected — the change has landed, and rejected also blocks re-proposal
- Multiple open proposals may touch the same skill (different patterns); since each branch is cut from the same base, such proposals can conflict at merge time — an accepted risk of branch-per-edit, not something the run prevents
- No per-run proposal cap; no semantic search over the store
- A pushed branch whose proposal is dropped at verification (e.g. an out-of-scope diff) stays on the remote — only worktrees and local branches are swept; its pattern remains eligible, and the next run's agent must pick a different (suffixed) branch name
- Reporting requires the tasks extension enabled — with it disabled, the dispatch event has no subscriber and the report is silently dropped
