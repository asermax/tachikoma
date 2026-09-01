# Design: Skill Evolution

<!-- This design describes the current implementation approach. Updated through delta reconciliation. -->

**Feature Spec**: [../feature-specs/skill-evolution.md](../feature-specs/skill-evolution.md)
**Status**: Current

## Purpose

This document explains how skills improve themselves from their own usage evidence: a fail-soft trunk-close pass that accumulates skill-friction patterns in a wiki-style store, a scoped proposal agent that authors one reviewable branch per pattern, and host-side reconciliation and verification that keep the ledger exactly as trustworthy as git.

## Problem Context

Skills improve only when the user notices a gap and requests a change. The evidence that a skill needs work — invocations that failed, guidance that was misapplied, stale, or duplicated, workarounds the agent improvised, workflows whose purpose has disappeared — is already in each day's conversation transcripts, but nothing reads it back into the skills.

The delivery mechanism for a proposed improvement is constrained by the workspace's architecture: the workspace is a git repo shared through an `origin` remote, the live working tree is never to be mutated experimentally, and the user must review every change (skills steer the agent's behavior — a bad auto-applied edit compounds silently). So a proposal has to be a reviewable artifact: a pushed branch, one per proposed change.

**Constraints:**

- Analysis rides the existing trunk-close pass: the same topic-branch walk memory post-processing uses, with no duplicated walk logic, running concurrently with it rather than serialized behind it
- The walk's idempotency markers, the store's wiki conventions, and the scoped-agent tool patterns all have established idioms in this codebase ([DES-002](../design/DES-002-extension-authoring.md), [DES-008](../design/DES-008-marker-computed-effective-state.md)); this feature reuses them rather than inventing parallels
- [DES-002](../design/DES-002-extension-authoring.md) bars direct imports between extension directories — every shared surface (walk, git helpers, markdown-store helpers, the task-dispatch contract) lives in a neutral module
- Nothing may block trunk close: this run is fail-soft at its boundary where memory's pipeline is deliberately fail-loud
- The proposal agent must never reach the default branch, existing remote branches, history rewrites, or files outside `skills/` — enforced by tool surface and host verification, not by prompt alone
- No accounting of squash merges beyond "rejected": the change has landed, and rejected also blocks re-proposal

**Interactions:**

- Memory post-processing (see [memory](memory.md)): shares the branch walk (`src/sessions/branch-walk.ts`) and the store-convention helpers (`src/util/markdown-store.ts`), and the `main` phase; the two processors write disjoint directories (`memories/{episodic,topics,learnings}` vs `memories/skill-evolution`)
- Git workspace (see [git-workspace](git-workspace.md)): the `finalize`-phase workspace `git-commit` processor is the backstop that commits this extension's store writes (it needs no commit of its own); memory's maintenance runs `commitAll` mid-close in the same phase and may capture a partially-written pattern page — writes converge when the finalize commit picks up the remainder, only commit grouping splits. The agent bash guardrail blocking `git push` is why the proposal agent gets a purpose-built git tool instead of bash
- Tasks (see [tasks](tasks.md)): the reporter dispatches an ad-hoc background instance through the `task:dispatch-background` app event; the existing 60 s tick executes it
- Skills (see [skills](skills.md)): the proposal agent changes the workspace skill packages this extension contributes; built-in bundled skills are out of scope as *proposal targets* — the two bundled authoring guides (`skill-authoring`, `workflow-authoring`) are loaded into the proposal run as reference skills, grounding authored changes in the house authoring conventions (see the force-loading Key Decision below)
- Coordinator (see [conversation-loop](conversation-loop.md)): provides the trunk context, the pre-filtered `branchRecords` snapshot, and the per-close `status` surface

## Design Overview

One extension, `src/extensions/skill-evolution/` (registered in `firstPartyExtensions`, `src/extensions/index.ts`), registering a single `main`-phase trunk-close processor (`skill-evolution-trunk-close`), a layout bootstrap, and a main-scoped agent-facing usage section (`usage.ts`, R16 — what the pass is and that proposals are reviewable branches, with a `references/skill-evolution.md` pointer per issue-445's two-tier convention). The processor runs the nightly loop:

```
trunk close (main phase, ∥ memory-trunk-close)
  ├─ origin gate      hasRemote(workspace, "origin")? else: log + skip entirely
  ├─ reconcile        git fetch origin --prune ── fails? soft-abort
  │                   per proposed log entry: reachability ─► accepted | pending | rejected
  ├─ analyze          shared branch walk: per unanalyzed topic branch, forkAndContinue
  │                   under FILE_EDIT_TOOLS ── writes pattern pages + index
  │                   then in-run maintenance pass (dedup, caps, sweep)
  ├─ propose          host filters eligible patterns (never re-proposed, input-side)
  │                   ── one side.run agent authors one branch per proposal
  │                   in a temp worktree: change ─► commit ─► push
  ├─ verify           host reads git state only: ls-remote namespace scan + diff-scope
  │                   check ── verified proposals get impact-log rows
  └─ report           ≥1 verified ── emit dispatch-background-task event
  fail-soft wrapper: any throw ── log + notify warning; close proceeds
```

Everything agent-driven uses the two established pi shapes (see [../reference/pi-sdk-notes.md](../reference/pi-sdk-notes.md); the selection rule is [DES-011](../design/DES-011-post-processing-agent-shapes.md)): the per-branch analysis is **conversation-aware** (`forkAndContinue` — the branch's turns live in the fork's history), and the maintenance/proposal passes are **context-free** headless `SideRunner.run`s with composed prompts and scoped custom tools. Everything bookkeeping-shaped (fetch, classification, verification, impact-log writes, cleanup) is deterministic host code — the host never trusts an agent's self-report for state that git can answer ([DES-010](../design/DES-010-agent-driven-git-host-verified.md)).

## Components

### Implementation Structure

| Layer/Component | Responsibility | Key Decisions |
|-----------------|----------------|---------------|
| `src/extensions/skill-evolution/index.ts` | Wiring: config schema (`enabled` default true, `postWorkPrompt` optional), layout bootstrap, main-scoped usage-section registration, processor registration | `defineExtension` per [DES-002](../design/DES-002-extension-authoring.md); early-return when `enabled` is false; workspace root captured in closure (processors don't receive it); the processor's stage seams (`hasRemote`/`reconcile`/`analyze`/`maintenance`/`proposal`/`verify`/`sweep`/`report`) each default to the real implementation and are individually overridable in tests |
| `src/extensions/skill-evolution/layout.ts` | `skillEvolutionDir(root)`, `ensureSkillEvolutionLayout` (seed `MEMORY.md` + `skill-impact-log.md` only when absent) | Own path helper ([DES-002](../design/DES-002-extension-authoring.md) — no importing memory's); seeding follows the `ensureMemoryLayout` idiom: mkdir recursive, skip-if-exists so user edits survive |
| `src/extensions/skill-evolution/store.ts` | Impact-log read/parse/write (row CRUD by branch+tip), pattern-page inventory for prompt assembly, `filterEligible` | Lenient markdown-table parsing — malformed rows logged and skipped, never fatal; the inventory excludes `MEMORY.md` and `skill-impact-log.md`; the eligible-pattern filter (exclude patterns with any impact-log entry — `proposed`, `accepted`, or `rejected`) lives here, enforcing never-re-proposed input-side |
| `src/extensions/skill-evolution/reconcile.ts` | Fetch + classification + log status updates | `runGitCapture` (branch on exit codes); reachability-first classification; every outcome a logged branch; the status write-back fires only when at least one row changed, so a fully-pending or empty ledger run is a byte-level no-op on the log file |
| `src/extensions/skill-evolution/analyze.ts` | Analysis-fork instruction builder, per-branch body for the shared walk, maintenance-pass prompt + invocation | Composed prompt sections (conventions mirror memory's `prompts.ts`); per-branch suffix stamps the trunk's day; maintenance via `side.run` + host sweep after |
| `src/extensions/skill-evolution/propose.ts` | Proposal-agent input assembly, scoped tool construction, `side.run` invocation, report capture | Custom tools only — built-ins are cwd-bound to the workspace root, so file tools are custom and path-validated under the tmp dir; the run opens `isolatePrompt: true` over `side.run`'s default empty built-in allowlist, so the six custom tools are the entire surface; the authoring guides ride the run as actual skills — `skillPaths: [builtinSkillsDir]` (loader-level discovery, composes with `noSkills`) and `forceLoadSkills` (hidden `skill-content` injection from pi's catalog), so the only skills in the run are the two guides; `report_proposals` closure-capture tool is the terminal step (the tasks `update_goal` idiom); a dying run throws `ProposalRunError` carrying whatever was captured, so verification still runs over the partial report |
| `src/extensions/skill-evolution/verify.ts` | Post-run verification, impact-log appends, cleanup sweep | `ls-remote` namespace scan (one call), diff-scope check, `finally` sweep of tmp worktrees + local `skill-evolution/*` branches + `worktree prune`; `sweepProposalArtifacts` is exported so the processor's no-proposal path sweeps too |
| `src/extensions/skill-evolution/report.ts` | Reporter: build prompt/goal, emit the dispatch event | Default prompt is notification-only; run summary inline |
| `src/extensions/skill-evolution/prompts.ts` | Shared prompt sections: store conventions, silent-background, proposal system prompt | Policy-as-prompt-sections like memory's `prompts.ts`; the conventions section is shared by analysis and maintenance, carries no `{date}` token, and names `skill-impact-log.md` as never-read-never-written by agents |
| `src/sessions/branch-walk.ts` *(core)* | `walkBranches` — the shared topic-branch walk (also used by memory extraction) | Neutral module next to `trunk.ts` ([DES-002](../design/DES-002-extension-authoring.md)); see Cross-Layer Contracts |
| `src/git/remote.ts` *(core)* | `fetchRemote(cwd)` (`fetch origin --prune`), `resolveRemoteDefaultBranch(cwd)` (symbolic-ref → `ls-remote --symref` → error), `listRemoteBranchTips(cwd, pattern)` | Git plumbing in the core git module alongside `sync.ts`; `execFile` via `runGit`/`runGitCapture` — no shell strings, and the split is meaningful: fetch returns its result (a non-zero exit is a soft-abort signal, not an exception), listing throws (a failed listing is exceptional); also used by projects' default-branch resolution |
| `src/util/markdown-store.ts` *(core)* | `listMarkdown`, `sweepEmptyMarkdown`, `isBlankMarkdown` | Shared wiki-store helpers (also used by memory); `sweepEmptyMarkdown` never removes a store's `MEMORY.md` index (structural even when blank) and takes a preserve list for other structural files — this extension's sweep sites pass `skill-impact-log.md` |
| `src/events.ts` *(core)* | `DISPATCH_BACKGROUND_TASK_EVENT` + `DispatchBackgroundTaskPayload` (`{prompt, goal?, source}`); the notify event constant + payload type also live here | Cross-extension event names as neutral module constants; the tasks extension subscribes via `src/extensions/tasks/dispatch.ts` |

### Cross-Layer Contracts

**Shared walk** (`src/sessions/branch-walk.ts`):

```ts
export interface BranchWalkProgress {
  start: (i: number, n: number) => string;   // "Analyzing skills — branch i/n"
  done: (i: number, n: number) => string;
  failed: (i: number, n: number) => string;
}
export interface BranchWalkDeps {
  agent: Pick<AgentManager, "forkAndContinue" | "branchFile">;
  body: (record: BranchRecord, branchFilePath: string) => Promise<void>;  // caller's forks/sweeps
  isDone: (session: AgentSession, record: BranchRecord) => boolean;
  markDone: (session: AgentSession, record: BranchRecord) => void;
  log: Logger;
  status?: (text: string) => void;
  progress?: BranchWalkProgress;             // caller-shaped lines, not one label
}
export function walkBranches(session, sessionFile, records: BranchRecord[],
                             day: string, deps: BranchWalkDeps): Promise<{ failed: BranchRecord[] }>
```

The walk owns: skip-if-marked, the `branchFile` cut (undefined ⇒ warn + skip, not failure), per-branch error isolation, the `finally { rm(branchFile) }` guarantee (body settles first), marker-after-body, and progress lines. The body owns everything domain-specific; so does the marker key — memory's pair matches `record.branchId`, skill-evolution's matches `record.summaryEntryId` (stable when reversals renumber the topic set). Memory's `extractBranches` builds its per-store forks as the body and throws if `failed.length > 0` (its keep-trunk-unclosed semantics); skill-evolution's body is one analysis fork + sweep, and it logs `failed` and continues.

**Proposal-agent tool surface** (built in `propose.ts`):

```
tmp dir = proposalTmpDir(workspace) = {os-tmp}/tachikoma-skill-evolution/<hash of workspace
    root> — stable per workspace (the next run's sweep must find this run's orphans) and
    outside the workspace repo entirely, so worktrees can never dirty the live tree
skills = exactly the two bundled authoring guides (skill-authoring, workflow-authoring):
    skillPaths: [builtinSkillsDir] (loader discovery — composes with noSkills, so no
    workspace/user skills leak in) + forceLoadSkills (one hidden skill-content message
    carrying both bodies) — reference material grounding the authored changes, never
    proposal targets
read_file(path) / write_file(path) / delete_path(path) / list_dir(path)   — path
    must resolve under the tmp dir (the worktrees live there); delete_path
    removes a file or directory recursively — how removals are authored, from
    redundant guidance to a whole skills/<name>/ directory — and additionally
    refuses the tmp dir itself (the path guard is root-inclusive, and the tmp
    dir holds every worktree) and a missing path (instructive, pointing at
    list_dir)
git({args, path?})          — allowlist: worktree(add|remove, path under tmp dir,
    -b name matching ^skill-evolution/), status, diff, log, show, add, commit
    (-c core.editor=true -c commit.gpgsign=false), push(origin, name) with:
    name ^skill-evolution/[a-z0-9][a-z0-9-]*$ AND name ≠ default branch AND
    refs/remotes/origin/<name> absent AND no --force
    (a collision suffix mutates the slug segment and must re-match the regex)
git's `path` is a plain cwd for every subcommand except add/commit — the one pair that
    mutates a working tree requires it to name a worktree under the tmp dir (missing or
    outside ⇒ refusal; a plain cwd there would stage/commit the live tree) and executes
    at exactly the validated path, so a relative `path` can never validate and execute
    against different bases (`worktree` positionals resolve the same way — against the
    run's cwd, git's own base — before the containment check)
report_proposals(proposals: [{branch, skill, pattern, description}])  — required
    terminal call; captured in a closure variable the host reads after the run
```

**Integration points:**

- The processor is fail-soft at its boundary (catch-all → warn log + notify warning); inside, each stage's failures degrade independently (a fetch failure aborts before analysis; a walk failure skips to no proposals; a proposal failure still verifies whatever pushed — the captured report flows into verification, and the error is re-thrown only after verify and report settle)
- Store writes need no commit of their own: the `finalize`-phase workspace `git-commit` processor is the backstop committing everything after `main` (see [git-workspace](git-workspace.md))
- `status` lines (optional-chained): `Reconciling skill proposals…`, `Analyzing skills — branch i/n`, `Verifying N proposals…`; `statusLabel: "Evolving skills"` for the close's lead-in line
- Headless/background closes (`trunk == null`) no-op before anything else

### Shared Logic

- **The branch walk** (`src/sessions/branch-walk.ts`): shared by memory and skill-evolution — the "no duplicated walk" rule is structural, not conventional. The exclusion rules (tangents, reversed summaries) stay upstream in `getBranchRecords`, which both consumers receive via the coordinator's snapshot; the walk never re-enumerates
- **Markdown-store helpers** (`src/util/markdown-store.ts`): shared sweep/list/blank-check so the wiki conventions (empty-file deletion protocol, `MEMORY.md` preservation) remain one implementation
- **Git remote plumbing** (`src/git/remote.ts`): fetch/default-branch/ls-remote helpers; `resolveDefaultBranch` in `src/extensions/projects/git.ts` delegates to the core resolver (best-effort `"main"` fallback as caller policy) so there is one resolution path
- **Marker machinery** (`src/sessions/trunk.ts`): one completion-marker mechanism, two payload kinds (memory's `branchExtracted`, this feature's `skillEvolutionAnalyzed`) — [DES-008](../design/DES-008-marker-computed-effective-state.md)'s one-chokepoint rule
- **Scoped git-agent tooling** (`src/git/agent-tools.ts`): the SideRunner slice, stage-and-commit allowlist, and result shaping shared by the commit agent, the rebase resolver, and this feature's proposal agent ([DES-010](../design/DES-010-agent-driven-git-host-verified.md))

## Modeling

```
memories/skill-evolution/
├─ MEMORY.md                # index: one line per pattern page
│                            `- [Title](./slug.md): PROBLEM — ROOT CAUSE — FIX`
├─ <pattern-slug>.md         # per-pattern page (~50-line cap): Problem / Root cause /
│                            #   Fix / Evidence (dated, updated-not-duplicated)
└─ skill-impact-log.md       # host-written proposal ledger (markdown table)

Pattern page 1—1 Skill (loose: a pattern names the skill it indicts)
Pattern page 1—* ImpactLogEntry   (each proposal for that pattern)
ImpactLogEntry: { date, skill, pattern link, branch name, tip SHA, description, status }
```

Proposal status lifecycle:

```
                 push verified (host, from git)
 proposal made ────────────────────────────────► proposed
                                                    │ next run, after fetch:
                                                    │ tip reachable from origin/<default> ─► accepted
                                                    │ branch still on remote ─────────────► pending (= stays proposed)
                                                    │ branch gone / object gone ──────────► rejected
                                                    ▼
                    accepted ∪ rejected both permanently block re-proposal of that pattern
```

Markers: one `skillEvolutionAnalyzed` completion marker per analyzed branch on the trunk session file, keyed by `summaryEntryId` (stable across topic renumbering) — at-most-once analysis per branch, surviving crash-recovery re-closes, retiring with the trunk.

## Data Flow

```
coordinator.closeTrunkSession
  └─ runPhasedPostProcessors ── main phase (allSettled, ∥ memory-trunk-close)
       └─ skill-evolution-trunk-close.process({trunk, log, status})
            ├─ trunk == null? → return                     (headless no-op)
            ├─ !hasRemote(origin)? → log "skipped: no origin", return
            ├─ reconcile:  fetchRemote ──► classify each proposed row ──►
            │              host rewrites Status cells in skill-impact-log.md
            ├─ walkBranches(shared): per unmarked topic branch
            │      branchFile(trunk.sessionFile, record.originalLeafId)
            │      ──► forkAndContinue(analysis instruction, processor, FILE_EDIT_TOOLS)
            │      ──► agent reads skills/ + pattern pages, updates pages + MEMORY.md
            │      ──► markDone(skillEvolutionAnalyzed) ──► rm branch file
            ├─ maintenance: side.run over store ──► dedup/caps ──► host sweep
            ├─ eligible = filterEligible(patterns, impactLog)
            │      (drop patterns with any impact-log entry: proposed | accepted | rejected)
            ├─ eligible empty? → namespace sweep ──► done (no LLM call, no proposals, no report)
            ├─ propose: side.run({ system, prompt(eligible + skills inventory + log),
            │      customTools: [read_file, write_file, delete_path, list_dir,
            │      git, report_proposals],
            │      skillPaths: [builtinSkillsDir], forceLoadSkills: [skill-authoring,
            │      workflow-authoring] })
            │      agent: per proposal ── git worktree add -b skill-evolution/<skill>-<slug>
            │              refs/remotes/origin/<default> ──► create/edit/delete files
            │              under skills/ ──► commit ──► push origin <name>
            │              ──► report_proposals([...])
            ├─ verify (finally-wrapped): listRemoteBranchTips("skill-evolution/*")
            │      ── per reported proposal: on remote? diff-scope only skills/?
            │      ──► append impact-log row {status: proposed, tip: remote sha}
            │      cleanup: rm worktrees under tmp dir ──► delete local
            │      skill-evolution/* branches ──► git worktree prune
            └─ verified ≥ 1? → report: emit DISPATCH_BACKGROUND_TASK_EVENT
                   {prompt: postWorkPrompt ?? default, goal: run summary, source}
                   ──► tasks subscriber createAdHocInstance(pending background)
                   ──► tasks-tick (≤60 s) ──► openBackgroundSession runs the prompt
```

Error paths: any stage throw → caught at the processor boundary → warn log + notify warning; completed branches keep their markers and store writes; the trunk still retires. A fetch failure returns before classification. A proposal-agent death leaves the `finally` sweep running (host-side, not agent-side) so no worktree or local branch survives, and whatever pushed and verifies is still logged and reported.

## Key Decisions

### Same-phase processor for parallel-with-memory, not a new phase or a cron

**Choice**: `skill-evolution-trunk-close` registers as a `main`-phase post-processor and relies on the phased runner's within-phase `Promise.allSettled` to run concurrently with `memory-trunk-close`.
**Why**: Parallel-with-memory semantics come for free (`src/extensions/post-processing.ts` — same-phase processors run concurrently, phases strictly sequential). A new phase would serialize the two walks (doubling close latency for no benefit); a standalone cron would decouple from the trunk lifecycle and lose the branch-records snapshot and the shared-close context.
**Alternatives Considered**: a `preFinalize` slot (serializes after memory); an independent nightly cron (no trunk context).
**Consequences**: Pro — zero new scheduling machinery; the two walks are concurrent by construction. Con — the two processors share the trunk session object and the working tree, so all shared surfaces must be concurrency-safe (walk cut: safe by detached-manager design; stores: disjoint dirs; commits: this extension never commits — memory's mid-close `commitAll` may capture a partially-written page, but writes converge at the finalize backstop).

### Shared walk in a neutral module, parameterized by body and markers

**Choice**: `walkBranches` in `src/sessions/branch-walk.ts` owns the walk skeleton (skip-if-marked → cut → body → cleanup → marker → isolate failures) and takes the domain-specific parts — per-branch body, marker pair, log/status, progress formatters — as dependencies. Memory's extraction runs on it too.
**Why**: [DES-002](../design/DES-002-extension-authoring.md) bars skill-evolution from importing memory's `extractBranches`, and the walk is the only duplicated-by-default part. Marker stores differ by design (memory: `branchExtracted` kind keyed by `branchId`; skill-evolution: `skillEvolutionAnalyzed` kind keyed by `summaryEntryId`), so the marker pair is the parameterization point. Returning the failure list (rather than a failure-mode flag) lets each caller keep its own semantics — memory throws to keep the trunk unclosed; skill-evolution logs and continues. Progress lines are caller-shaped formatter functions (`start`/`done`/`failed` over `i/n`), not a single label: memory's three templates and skill-evolution's analysis line are different shapes one label cannot reproduce.
**Alternatives Considered**: memory exports the walk and skill-evolution imports it (cross-extension import, [DES-002](../design/DES-002-extension-authoring.md) violation); duplicating the loop (the settle-then-delete subtlety is exactly what shouldn't be copied twice); a failure-mode enum inside the walk (pushes policy into shared code).
**Consequences**: Pro — one implementation of a subtle loop (settle-then-delete, detached-manager rule); memory's behavior is unchanged. Con — memory's close-pipeline is coupled to the shared module (refactor risk, covered by its existing tests).

### Session-file markers via an additive marker kind (DES-008)

**Choice**: Per-branch "analyzed" markers are `tachikoma-completion-marker` entries with the payload kind `skillEvolutionAnalyzed` (`{summaryEntryId}`) on the trunk session file, matched by `summaryEntryId`.
**Why**: The marker mechanism is the codebase's single idiom for append-only idempotency state ([DES-008](../design/DES-008-marker-computed-effective-state.md)) — one chokepoint, `hasMarker` scans, native reload. The lifecycle is automatic: the marker retires with the trunk transcript, so "unanalyzed branches of a failed day are abandoned forever" needs no garbage collection, and skip-on-re-close is the same read memory already does. Concurrent appends from the two same-phase processors serialize through the same session-manager object. The payload key is the branch summary's entry id, not the `topic-N` branch id: `toRecord` renumbers positions over the filtered topic set, so a `/rollback` reversal after a failed close would shift every later `topic-N` — [DES-008](../design/DES-008-marker-computed-effective-state.md)'s stable-key rule ("entry ids, not positions") exists for exactly this case, and the crash-recovery re-close is the window where it bites.
**Alternatives Considered**: `app.state` KV (keys accumulate across trunk retirements with no enumeration/GC surface, and it forks the marker idiom); a DB table ([DES-002](../design/DES-002-extension-authoring.md) reserves structured persistence for query-shaped state; this is marker-shaped).
**Consequences**: Pro — one marker mechanism; automatic lifecycle; crash-recovery re-closes skip marked branches for free. Con — core `sessions/trunk.ts` carries a second trunk-close kind (small, additive; the discriminated union keeps it compile-time-checked).

### Reconciliation runs its own `fetch --prune`; classification is reachability-first

**Choice**: `git fetch origin --prune` at the top of every run; a non-zero result soft-aborts before any classification or analysis, and so does a failure to resolve the remote default branch. Classification per recorded tip: `merge-base --is-ancestor <tip> refs/remotes/origin/<default>` — exit 0 → accepted (branch may or may not still exist); exit 1 → the remote-tracking branch still exists → pending; branch gone → rejected; any other exit with a resolved ref (unresolvable object, e.g. post-squash-merge GC) → rejected, which is also the documented squash-merge outcome (a merge commit keeps the tip reachable, so a missing object cannot have been merge-merged). The branch-presence probe is `rev-parse --verify --quiet` on the tracking ref (exit code only).
**Why**: Nothing earlier in the same close fetches the workspace — memory's pipeline never touches the network, the previous fetch was startup's `smartPull` or the last session's finalize push (hours stale), and finalize's fetch runs after `main`. `--prune` is what makes "branch still present" meaningful: remote deletions only reach local tracking refs through it, and overnight is exactly when the user merges or deletes proposal branches. The exit-code ladder falls out of `git-merge-base(1)`: `--is-ancestor` exits 0 (ancestor), 1 (not), other (error). The default-branch abort exists because classifying against an unresolved ref would mass-reject every open proposal irreversibly — the one outcome the abort exists to prevent.
**Alternatives Considered**: relying on a prior fetch (stale; misclassifies overnight merges/deletions); `git branch -r --contains <sha>` (equivalent information, more output to parse, same missing-object failure); `git remote show origin` for the default branch (network-slow; `symbolic-ref` first is offline, `ls-remote --symref` as the online fallback).
**Consequences**: Pro — classification always judges fresh remote state; the fetch doubles as the run's connectivity probe; the log write-back fires only when a row actually changed (a fully-pending run is a byte-level no-op). Con — one network round trip per close; a hung remote delays the close until the fetch fails, and a default-branch resolution failure aborts the run (fail-soft still applies).

### Two agent shapes: fork-continue for analysis, headless run for maintenance and proposals ([DES-011](../design/DES-011-post-processing-agent-shapes.md))

**Choice**: The per-branch analysis fork carries the branch's conversation live (`forkAndContinue` + `FILE_EDIT_TOOLS`, persona intact — the same assistant that had the conversation reflects on it). The maintenance pass and the proposal agent are context-free `SideRunner.run`s with composed prompts and scoped custom tools.
**Why**: The two shapes are the SDK's documented post-processing patterns ([../reference/pi-sdk-notes.md](../reference/pi-sdk-notes.md)): conversation-aware work needs the turns live; store hygiene and branch authoring need no conversation, only inputs assembled by the host. Replaying transcripts as text is lossy — tool activity and thinking are where skill failures and workarounds actually show.
**Alternatives Considered**: a transcript-replay headless run for analysis (lossy, and the workarounds live in tool calls); a persona fork for the proposal agent (nothing conversational to inherit; the worktree protocol wants a clean system prompt).
**Consequences**: Pro — full-fidelity evidence where it matters; cheap, prompt-shaped, tool-scoped runs where it doesn't. Con — the analysis fork's cost scales with branch length (the same accepted trade as memory's extraction).

### The proposal agent drives git through an allowlisted custom tool, never bash

**Choice**: One `side.run` agent gets custom file tools (path-validated under the tmp dir) and a `git` tool allowing exactly: `worktree add` (path under tmp dir, `-b` name matching `skill-evolution/<skill>-<slug>`), `worktree remove`, `status`, `diff`, `log`, `show`, `add`, `commit` (with `core.editor=true`/`commit.gpgsign=false`), and `push origin <name>` — where push refuses the default branch, any force flag, and any name already present on the remote. The agent terminates via `report_proposals`, a structured closure-capture tool.
**Why**: The guardrail blocks `git push` in every bash-capable session, and the commit-agent/rebase-resolver precedent shows the pattern: an in-process `git` tool with a subcommand allowlist bound to the target tree ([DES-010](../design/DES-010-agent-driven-git-host-verified.md)). Argument-level validation makes branch naming structural: a non-`skill-evolution/` name, a default-branch push, or a push over an existing branch is refused at the tool boundary with a self-correcting error (the agent retries with a suffix — the collision rule executes itself). Refusing existing remote branches makes pushes effectively create-only, so no force flag can ever rewrite anything. The `add`/`commit` pair — the one working-tree-mutating pair — additionally requires `path` to name a worktree under the tmp dir and executes at exactly the validated path, so a plain cwd can never stage or commit the live tree. Removals ride the same pair: a `delete_path` file tool deletes the path and the existing `add` stages it (`git add <path>` records a deleted file or a fully-deleted directory — git ≥2.0 pathspec semantics), so the allowlist gains no new subcommand. The report tool follows the tasks `update_goal` idiom — a tool whose execute handler captures intent for the host to read after the turn.
**Alternatives Considered**: host-driven pushes (narrower surface, but the agent cannot react to a rejected push — rejected-and-rename-and-retry is the common case the agent handles in one run); granting bash with prompt-level restrictions (not enforcement); a planning call plus pre-allocated names (an extra model call for validation the tool already does); `git rm` in the allowlist (a second deletion path with its own `-r`/`--cached` flag matrix, for what the existing `add` already does — and every consumer of the shared allowlist would inherit a destructive git verb).
**Consequences**: Pro — the pushed blast radius is exactly "new `skill-evolution/*` branches" (in-run, the file tools can delete a worktree's `.git` marker, after which `add`/`commit` self-refuse and the `finally` sweep recovers); naming enforced mechanically; one model call per run. Con — the allowlist must stay conservative (maintained alongside the guardrail's denylist, as `commit-agent.ts`'s already is).

### Verification and the impact log are host-only, decided from git state

**Choice**: After the proposal run, the host scans the namespace once (`listRemoteBranchTips` → `ls-remote --heads origin 'skill-evolution/*'`), matches reported branch names, checks each verified branch's diff touches only `skills/` (three-dot `base...tip`, so an upstream default-branch advance between the reconcile fetch and the check cannot surface as out-of-scope drift), and appends impact-log rows with the *remote* tip SHA. The agent's report never creates or confirms an entry; reported-but-absent branches are warned and dropped.
**Why**: `ls-remote` is the authoritative remote view (local tracking refs predate the pushes); reading the tip from it removes any local/remote ambiguity. The diff-scope check makes "workspace skills only" host-enforced rather than prompt-enforced (built-in skills aren't in the workspace repo at all, so the check guards against drift outside `skills/`, not built-in edits). The impact log stays deterministic markdown bookkeeping — no LLM writes it, so no LLM can mis-record a SHA.
**Alternatives Considered**: trusting the report's SHAs; `git rev-parse` of local branches (local state; a partially-failed push would lie); verifying per-branch with individual `ls-remote` calls (N network calls for what one pattern-scoped call answers).
**Consequences**: Pro — the ledger is exactly as trustworthy as git; agent output can at worst cause an unlogged (still-eligible) pattern. Con — one extra network call per run; a branch pushed but reported with a mismatched name is unlogged (warned) — acceptable, the pattern stays eligible and re-proposes.

### Cleanup is a host-side `finally` sweep over namespaces, not tracked state ([DES-012](../design/DES-012-namespace-sweep-cleanup.md))

**Choice**: Verification wraps in `finally`: remove every worktree under the tmp dir (forced), delete every local `skill-evolution/*` branch, run `git worktree prune`. A run that proposes nothing sweeps directly instead — the same exported function, called by the processor — so every full pass sweeps exactly once. No per-proposal tracking of what was created.
**Why**: The feature owns both namespaces (the tmp dir and the branch prefix), so namespace sweeps are correct regardless of how many proposals were created, whether the agent reported, or where it died — the invariant "no stray worktrees or local branches survive" holds with no bookkeeping to maintain. The tmp dir is a stable per-workspace directory under the OS temp dir (a hash of the workspace root), so the next run's sweep finds this run's orphans across process restarts. The sweep order (worktrees, then branches, then prune) matters and is fixed: a branch checked out in a worktree blocks deletion, and `worktree remove --force` succeeds on both an unclean tree and a worktree whose directory was deleted out from under git (clearing the admin files either way), so no ordering strands a branch behind an undeletable admin entry.
**Alternatives Considered**: tracking created worktrees/branches in run state (a second source of truth that itself can be lost mid-crash); agent-side cleanup (the agent that died can't clean up after itself); a fresh `mkdtemp` per run (orphaned by construction — nothing later would know where to sweep).
**Consequences**: Pro — the cleanup guarantee is unconditional and trivially testable; a crashed run self-heals on the next run's sweep, which fires on every full pass. Con — a user-created local branch named `skill-evolution/…` is deleted (the namespace is documented as feature-owned — same convention as the branch naming rule).

### Reporter through a neutral app-event contract

**Choice**: `src/events.ts` carries `DISPATCH_BACKGROUND_TASK_EVENT` and its payload; skill-evolution emits it; the tasks extension subscribes (`src/extensions/tasks/dispatch.ts`) and creates the ad-hoc pending background instance. The prompt is `postWorkPrompt ?? default notification-only prompt`, seeded with the run summary (proposals, patterns, log path) and an explicit `goal` (which skips the background runner's goal-extraction call).
**Why**: [DES-002](../design/DES-002-extension-authoring.md) bars importing `TaskRepository` across extension dirs, and of the sanctioned channels (app service vs app event) the event matches the shape of the need: fire-and-forget, single producer/consumer pair, no return value. The neutral-constant pattern is the established one; the notify event contract lives in `src/events.ts` the same way (see [notifications](notifications.md)). The existing 60 s tick dispatches the instance with zero new execution machinery.
**Alternatives Considered**: an `app.tasks.createAdHocInstance` service (the `GitApi` precedent — fine, but adds AppContext surface for one call); direct repository import ([DES-002](../design/DES-002-extension-authoring.md) violation); calling `BackgroundRunner.tick()` after creation (unnecessary — the tick owns dispatch).
**Consequences**: Pro — task-creation logic stays inside tasks; skill-evolution depends only on the event contract; an invalid payload is dropped by the subscriber's validation (a blank/missing goal normalizes to `null`, a prompt-less payload warns and drops), never crashes the run. Con — the contract is loose (a payload schema, not a typed service call); if the tasks extension is disabled, reports vanish silently (documented limitation in the spec).

### Store mirrors the memory wiki conventions; the ledger is the one host-written file ([DES-013](../design/DES-013-markdown-wiki-store.md))

**Choice**: `memories/skill-evolution/` follows the memory store shape — `MEMORY.md` one-line index, per-pattern pages with dated Evidence sections, ~50-line caps, update-not-duplicate, empty-then-sweep deletion — all agent-enforced through prompt sections. `skill-impact-log.md` is the one exception: host-written, table rows, deterministic facts.
**Why**: The memory-store conventions are named requirements, so mirroring them reuses both the proven prompt idioms and the reader expectations (the same agent habits maintain both stores, via the shared `src/util/markdown-store.ts` helpers). The impact log is different in kind — SHAs, dates, and statuses come from git and reconciliation, where an LLM writer adds error, not judgment. Hosting the log also makes reconciliation (rewrite status cells) a plain file edit, and the prompt layer reinforces the split: the shared conventions section names `skill-impact-log.md` as never-read-never-written by agents.
**Alternatives Considered**: an agent-written ledger (LLM-written SHAs are a defect class); a DB table (the log belongs in the store and must be human-reviewable in the workspace); per-proposal log files (enumeration for "open proposals" becomes a directory scan for no gain).
**Consequences**: Pro — conventions are shared text, reviewable in git; the ledger never drifts from git. Con — the table parser must be lenient (user edits) — malformed rows logged and skipped.

### The authoring guides ride the proposal run as actual skills, force-loaded through pi's catalog

**Choice**: The proposal run passes `skillPaths: [builtinSkillsDir]` and `forceLoadSkills: ["skill-authoring", "workflow-authoring"]` (the generic agent-layer options; see [agent-integration](agent-integration.md)). `builtinSkillsDir` is the neutral `src/util/builtin-skills.ts` constant — [DES-002](../design/DES-002-extension-authoring.md) bars importing the skills extension's private path computation, and duplicating it would let a moved directory fail silently behind the fail-soft skip. The loader discovers the guides from the bundled directory — composing with `isolatePrompt`'s `noSkills`, so the run's catalog is exactly the two guides — and a `before_agent_start` injection reads both bodies from pi's catalog into one hidden `skill-content` message. The proposal system prompt's authoring-conventions rule points at them: every file created or edited under the worktree's `skills/` must conform (edits preserve established structure; removals leave the surviving skill coherent) — conditional on the guides' content being present in the run's input, so fail-soft loading and the policy never disagree (a run whose guides failed to load proceeds ungrounded rather than under a rule naming content that isn't there).
**Why**: The proposal agent authors workspace skills without knowing the house authoring conventions; the two bundled guides are those conventions. Loading them through pi's own machinery — not a parallel one — keeps the run's tool surface unchanged (empty built-in allowlist, six custom tools, tmp-dir confinement) and keeps the guides' formatting, frontmatter stripping, and discovery where the loader already owns them. The guides speak of runtime workflow/delegation tools this run does not have and of the workspace's `skills/` directory; the prompt rule maps both onto the proposal run (conventions only; the current proposal worktree). Conformance stays prompt-enforced — the verify stage keeps checking git facts only (branch exists, diff confined to `skills/`), consistent with [DES-010](../design/DES-010-agent-driven-git-host-verified.md): style is guidance for the authoring LLM, and the reviewer remains the final style authority.
**Alternatives Considered**: hand-inlining the guide bodies into the prompt or staging copies in the tmp dir and widening `read_file` (a second skill-loading mechanism that must mirror pi's formatting and breaks the everything-under-the-tmp-dir tool invariant — the maintainability cost this exists to avoid); a structural style linter over pushed diffs (new host machinery for marginal value).
**Consequences**: Pro — authored proposals follow the authoring standards with zero tool-surface or isolation change; loading is fail-soft (a missing guide warns and the run proceeds ungrounded). Con — the guide names are coupled between `prompts.ts` (the rule text) and `propose.ts` (the run wiring) — held together by the shared `AUTHORING_GUIDE_SKILLS` constant and its tests.

### Fail-soft at the processor boundary; markers make partial progress durable

**Choice**: The whole processor body is one try/catch → warn log + notify warning. Per-branch analysis markers persist as branches complete, so a mid-run failure leaves completed branches permanently analyzed and unanalyzed ones permanently abandoned.
**Why**: Skill-evolution is deliberately weaker than memory on failure: memory keeps the trunk unclosed to guarantee extraction; skill-evolution must never delay or block close. The marker-after-body rule ([DES-008](../design/DES-008-marker-computed-effective-state.md)) plus trunk retirement gives "abandoned forever" for free — the retired trunk is never re-walked.
**Alternatives Considered**: fail-loud like memory (delays close for a best-effort feature); re-queueing unanalyzed branches into the next run (the trunk's branch records leave with the trunk).
**Consequences**: Pro — close latency and success are unaffected by this feature. Con — a failure after some analyses loses the rest of that day's analysis permanently (accepted by the spec; the cost of never blocking close).

## System Behavior

### Scenario: Normal nightly run with proposals

**Given**: A trunk closing at the nightly boundary with topic branches, an origin remote, patterns accumulated in the store, and at least one eligible pattern
**When**: The processor runs
**Then**: Reconciliation updates yesterday's entries; each unmarked branch is forked and analyzed; the maintenance pass tidies the store; the proposal agent authors one branch per eligible pattern in temp worktrees and pushes them; the host verifies from `ls-remote` + diff scope, appends `proposed` rows, sweeps worktrees and local branches, and emits the dispatch event; the tasks tick runs the post-work prompt within 60 s.

### Scenario: Proposal merged as a merge commit

**Given**: An impact-log row in `proposed` whose branch the user merged into the default branch and deleted on the remote
**When**: The next run fetches and classifies
**Then**: The recorded tip is reachable from `refs/remotes/origin/<default>` (exit 0) → the row becomes `accepted`; the pattern is blocked from re-proposal forever.

### Scenario: Proposal merged via squash merge

**Given**: The user squash-merged the branch (new SHA on the default branch, recorded tip unreachable)
**When**: Classification runs
**Then**: Reachability fails, the branch is gone from the remote → `rejected`. Documented limitation: the change has landed; `rejected` still blocks re-proposal, which is the desired steady state.

### Scenario: Fetch fails (offline / read-only remote)

**Given**: `git fetch origin --prune` exits non-zero
**When**: Reconciliation starts
**Then**: The run soft-aborts — a warning is logged and notified, nothing is classified, no analysis forks run, no store writes happen this run. (Distinct from the no-origin gate, which skips silently.)

### Scenario: Push denied mid-proposal

**Given**: The first proposal pushed; the second push is denied (credentials revoked)
**When**: The proposal agent errors out
**Then**: The host verifies from git: the first branch exists on the remote and passes the diff-scope check → logged `proposed`; the second is absent → warned, unlogged. The `finally` sweep removes both worktrees and local branches; the run reports soft failure; the reporter still fires (≥1 verified). The unlogged pattern stays eligible for the next run.

### Scenario: Proposal agent dies between worktree creation and push

**Given**: The agent run throws after `git worktree add` but before any push
**When**: The run settles
**Then**: Verification finds no new remote branches → no log rows; the `finally` sweep removes the stray worktree(s) and any local `skill-evolution/*` branch; `worktree prune` clears administrative files. No retry this run — the patterns remain eligible (no log entries were written).

### Scenario: Whole-skill removal proposal

**Given**: An eligible pattern whose fix is retiring the skill (the workflow it describes no longer exists)

**When**: The proposal agent runs

**Then**: It deletes the skill's directory in the worktree (`delete_path skills/<name>/`), stages the removal with the same `add`, commits, and pushes one `skill-evolution/<skill>-<slug>` branch; the host's diff-scope check passes (deleted paths still resolve under `skills/`) and the impact-log row names the removed skill. A later pattern indicting the same skill is declined at propose time — each run cuts a fresh worktree from `refs/remotes/origin/<default>`, so the skill deleted by the merged proposal is absent from it.

### Scenario: Close re-runs after a memory failure

**Given**: Memory's processor threw, keeping the trunk unclosed; this extension had analyzed some branches (markers written)
**When**: The close re-runs
**Then**: The walk skips every branch carrying a `skillEvolutionAnalyzed` marker and analyzes only the remainder; reconciliation and proposal phases re-run idempotently (patterns already logged are excluded input-side, so no duplicates).

### Scenario: Headless background close with no trunk

**Given**: `runPostProcessors` invoked with `trunk: null` (a background task completion)
**When**: The processor runs
**Then**: Immediate no-op — no gate checks, no store writes.

### Scenario: Workspace without an origin remote

**Given**: `hasRemote(workspace, "origin")` is false
**When**: The trunk closes
**Then**: The run is skipped entirely — logged, no analysis forks, no run-time store writes, no log changes. The bootstrap-seeded layout is unaffected (it runs regardless). The toggle remains available for when an origin is added.

### Scenario: Malformed impact log after user edits

**Given**: A row is missing columns or a linked pattern page was deleted by the user
**When**: Reconciliation or proposal-input assembly reads the store
**Then**: The malformed entry is logged and skipped; classification proceeds over the well-formed rows; never fatal.

### Scenario: Branch with no skill-usage signal

**Given**: An analyzed branch where skills were used without incident
**When**: The analysis fork runs
**Then**: It may record nothing — a clean no-op for that branch (the marker is still written; not an error).

### Scenario: First run — empty impact log

**Given**: The bootstrap-seeded store (`MEMORY.md` with no pattern entries, `skill-impact-log.md` header-only) and a trunk closing with topic branches
**When**: The processor runs
**Then**: Reconciliation finds no `proposed` rows and is a no-op; analysis and maintenance proceed normally; every pattern page is eligible. If no patterns exist yet, the run ends after the walk — no proposal agent invocation, no report.

### Scenario: Extension disabled

**Given**: `enabled: false` under `[extensions.skill-evolution]`
**When**: The host starts and a trunk closes
**Then**: The extension registered nothing — no layout bootstrap, no processor; the close behaves as though the feature did not exist.

## Notes

- The analysis instruction and the maintenance system prompt share one store-conventions section kept free of the `{date}` token — only the analysis base prompt and its per-branch suffix carry the day (mirroring memory, where only fork instructions are dated). The silent-background section rides the analysis fork only; headless `side.run`s have no persona to silence
- git behaviors relied upon (verified against the installed git): `merge-base --is-ancestor` exits 0 (ancestor), 1 (not), 128 on an unresolvable argument — which is how a post-squash-GC tip rejects without probing branch presence; `symbolic-ref --short refs/remotes/origin/HEAD` prints the remote-prefixed `origin/<name>` (the resolver strips the prefix), and a clone made while origin was empty, or a repo wired up via `git init` + `remote add`, never gets that ref at all — the `ls-remote --symref` fallback's case; `worktree remove --force` succeeds on an unclean tree and on a worktree whose directory was deleted out from under git; the push guard's exact-three-token shape structurally excludes force/lease flags in any position; `git add <path>` stages a deleted file or a fully-deleted directory's removal (pathspec semantics, git ≥2.0) — why a delete tool plus the existing `add` covers removal proposals without `git rm`
- Partial-failure plumbing: reconciliation carries the resolved `defaultBranch` out to the proposal and verify stages (one resolution, threaded — resolving it again mid-run could itself throw and skip verification); a dying proposal run throws `ProposalRunError` carrying the captured report, so verification always runs over whatever was reported, and the error surfaces through the fail-soft boundary only after verify and report settle
- Reporting requires the tasks extension enabled — the dispatch event has no other subscriber (documented limitation in the spec)
- Testing ([DES-003](../design/DES-003-testing-conventions.md)): real git repos in temp dirs with a bare origin for reconcile/propose/verify/sweep (the `setupRemotePair` precedent); faked `SideRunner` and `BranchForker` for the agent paths; marker assertions against temp session files; event-emission assertions with a faked `EventBus`; the processor's stage seams fake structurally (no LLM, no git where a plain function answers)
