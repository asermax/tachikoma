# Skill Evolution

Nightly self-improvement of workspace skills. Owned by the skill-evolution extension.

## What happens at trunk close

The pass runs alongside memory extraction, on the same trunk session and branch records:

1. **Reconcile** — refreshes remote state and classifies yesterday's still-open proposals
   (accepted / pending / rejected). A fetch or default-branch failure aborts the run safely.
2. **Analyze** — one conversation-aware fork per unanalyzed topic branch looks for skill
   friction: places a skill was missing, wrong, stale, redundant, or fought the task.
3. **Maintain** — consolidates near-duplicate patterns and enforces the size caps.
4. **Propose** — for eligible recurring patterns, an agent authors one branch per pattern
   that changes the skill — its guidance, or its bundled tooling (e.g. fixing or adding a CLI
   command, with tests), or removing a skill that no longer serves a purpose — and pushes it
   to origin for review.
5. **Verify** — host-side, from git state: only branches that actually pushed are recorded.
6. **Report** — with ≥1 verified proposal, an ad-hoc background task is dispatched with the
   run context, including each proposal's full reasoning (what it does, the problem, the root
   cause, the evidence — restated from its pattern page): the material a pull-request body
   should carry. The default prompt is notification-only (summarize for the person, take no
   further action — no PRs, no merges, no edits); a configured `postWorkPrompt` replaces it.

A pattern with any impact-ledger entry never re-proposes, so an open proposal blocks
duplicates until it is resolved. The whole run is fail-soft: any failure warns and lets the
trunk close continue.

## The store

`memories/skill-evolution/` holds one page per pattern (`PROBLEM — ROOT CAUSE — FIX`
summarized in its `MEMORY.md` index) plus the impact ledger the eligibility rule reads. Read
freely; do not hand-edit — the pass maintains it.

## Your role

None of this requires you. Where you come in: reading pattern pages when a recurring
friction is relevant, explaining a proposal when asked (it is a plain branch on origin), and
knowing that live skills are never modified without review.

## Configuration

`[extensions.skill-evolution]`: `enabled` (default `true`) — the whole pass is skipped when
disabled; `postWorkPrompt` (optional) replaces the notification-only default the reporting
task runs. The pass also requires an `origin` remote — no remote, no proposals.
