# Design: Workspace Git Versioning

<!-- This design describes the current implementation approach. Updated through delta reconciliation. -->

**Feature Spec**: [../feature-specs/git-workspace.md](../feature-specs/git-workspace.md)
**Status**: Current

## Purpose

This document explains how the workspace stays version-tracked unattended: why commits are a single staged pass with an LLM-generated subject line, why sync helpers return result enums instead of throwing, how the same primitives serve both the workspace and registered projects, and why destructive history operations are funneled through one explicit tool while the agent's raw bash path is gated against them.

## Problem Context

Every session leaves the workspace mutated — memory extraction, context edits, project pointer bumps — and none of it should require manual git operations. The same workspace may be shared across machines through an `origin` remote, so a bare `git push` would eventually hit non-fast-forward rejections, and any conflict handling must work without a human present.

**Constraints:**
- Runs unattended at session close and at startup — no prompts, no GPG, no reliance on the user's global git config
- Post-processing must never fail the session close: every outcome degrades to a log line with local commits preserved
- Linear history on a single branch — divergence is resolved by rebasing local commits onto the remote, never by merge commits or force pushes
- The primitives must work in plain repos, submodules (gitlink `.git` files), and repos created via `git init` that lack an `origin/HEAD` ref

**Interactions:**
- The [projects](projects.md) extension imports `commitAll`, `smartPull`, and `smartPush` directly and runs its processor in `preFinalize`, so submodule pointer updates are already on disk when `git-commit` runs in `finalize`
- Post-processing phases and session close are orchestrated by the coordinator ([conversation-loop.md](conversation-loop.md)); memory extraction writes the files this processor commits ([memory.md](memory.md))
- Side completions go through `app.agent.side` at the `processor` tier ([DES-001](../design/DES-001-unified-extension-api.md))

## Design Overview

The extension (`src/extensions/git/index.ts`) wires a bootstrap hook, two pi extension factories (a tools factory and a bash guardrail factory), and a finalize-phase post-processor. The reusable git *primitives* are no longer part of this extension: they live in a neutral **core module** `src/git/` — `git.ts` (promisified `execFile` subprocess primitives that never shell out), `commit.ts` (stage-all + message generation), and `sync.ts` (divergence classification and the push/pull state machines, including the `RebaseResolver` type). The extension consumes them directly and also exposes the high-level operations (`commitAll`, `smartPush`, `smartPull`) to all extensions through the **`app.git`** service (see [DES-001](../design/DES-001-unified-extension-api.md)), so consumers like [projects](projects.md) and [memory](memory.md) no longer import the git extension. What remains under `src/extensions/git/` is purely extension-specific: `scrub.ts` (history rewrite via `git filter-repo`), `guardrail.ts` (compound-command splitting + the destructive deny patterns + the `tool_call` interceptor), `resolve.ts`, `tools.ts`, `processor.ts`, and `hooks.ts`. Commit, sync, and scrub take a `cwd`, so the same primitives serve the workspace, registered projects, and the memory store.

```
session close ─► git-commit (finalize)
                   ├─ commitAll: add -A → diffstat → side.complete → commit
                   └─ smartPush: abort stale rebase → fetch → detectDivergence
                        ├─ AHEAD     → push → PUSHED
                        ├─ DIVERGED  → rebase --autostash
                        │               ├─ clean    → push → REBASE_SUCCEEDED
                        │               ├─ conflict → resolver agent ×N → push → AGENT_RESOLVED
                        │               │               └─ unresolved → abort → REBASE_FAILED
                        │               ├─ no-start → REBASE_FAILED
                        │               └─ rebase ok but push fails → PUSH_FAILED
                        └─ UP_TO_DATE/BEHIND → NOTHING_TO_PUSH
```

Note: a rebase that succeeds (clean or agent-resolved) followed by a *push* failure returns `PUSH_FAILED`, distinct from `REBASE_FAILED`. On the pull side, `smartPull` treats a local-*ahead* branch the same as equal — both return `UP_TO_DATE` (it never pushes from a pull).

## Components

### Implementation Structure

| Component | Responsibility | Key Decisions |
|-----------|----------------|---------------|
| `src/git/git.ts` *(core module)* | Subprocess primitives: `runGit` (throws), `runGitCapture` (never throws), `hasUncommittedChanges`, `hasRemote` | `execFile`-based, no shell strings (DES-002); capture variant returns `{code, stdout, stderr}` so callers branch on exit codes. Core utility — any extension may import it directly for low-level ops |
| `src/git/commit.ts` *(core module)* | `commitAll`: stage everything, generate message from diffstat, commit | `Completer = Pick<SideRunner, "complete">` for fakeable tests; sanitization + 100-char cap; explicit `message` skips generation. Exposed to extensions via `app.git.commitAll` |
| `src/git/sync.ts` *(core module)* | `detectDivergence`, `smartPush`, `smartPull`, the `RebaseResolver` type, result const maps | merge-base ancestor checks; stale-rebase abort on entry; gitlink and `HEAD`-name resolution; optional `RebaseResolver` for conflicts with a bounded attempt loop; never throws. `smartPush`/`smartPull` exposed via `app.git` |
| `src/extensions/git/index.ts` | `defineExtension` wiring; honors `enabled` flag | Hook + tools factory + processor; no logic |
| `src/extensions/git/resolve.ts` | `createGitResolver`: a `RebaseResolver` (type from `src/git/sync.ts`) backed by a headless side agent, with cwd-scoped `read_conflict`/`write_resolved`/`git` custom tools | One side-agent pass per call; tools bound to the target repo so the agent can't touch the wrong tree; `git` tool rejects push/fetch/reset/remote/filter-repo; swallows agent errors so a sync is never aborted by a throw |
| `src/extensions/git/hooks.ts` | `initializeWorkspaceRepo`: init + identity + gitignore + startup `smartPull` | Fixed identity, `commit.gpgsign false`; gitignore entries appended uncommitted on every startup |
| `src/extensions/git/processor.ts` | `git-commit` post-processor (`finalize`) | Commit → push when `origin` exists → verify clean → one retry commit |
| `src/extensions/git/tools.ts` | `query_git_status`, `list_recent_commits`, `commit_workspace`, `scrub` | Handlers exported standalone; outputs truncated with pi's `truncateTail`; scrub handler resolves the target repo (workspace, or `projects/<name>` when `project` is given), validates it is non-empty and exists, delegates to `scrubPaths`, and surfaces its outcome message (plus a stale submodule-pointer note on a successful project scrub) |
| `src/extensions/git/scrub.ts` | `scrubPaths`: clean-tree + path-existence + tool-availability checks, `git filter-repo --invert-paths`, origin restore + force-push | Returns a `SCRUB_RESULT` enum outcome, never throws; `isFilterRepoAvailable` probes `git filter-repo --version` so a missing tool is a clean error, not a crash |
| `src/extensions/git/guardrail.ts` | `DESTRUCTIVE_GIT_DENY_PATTERNS`, `splitCompoundCommands`, `findDeniedSubcommand`, `createGitGuardrailFactory` | A `tool_call` interceptor that blocks the `bash` tool on destructive git; quoting-aware compound split so a destructive sub-command can't hide behind quotes or operators |

## Key Decisions

### Single diffstat completion instead of a commit agent

**Choice**: One `git add -A`, one `side.complete` call over the staged diffstat producing a single subject line, one commit.
**Why**: A plain completion is cheaper, faster, fully fakeable in tests, and exposes no tool surface to constrain — and a per-session bulk commit is granular enough for a workspace whose history exists for review and rollback.
**Alternatives Considered**:
- Headless agent run (`app.agent.side.run`) grouping commits: better commit granularity, but slower, costlier, and needs git-command guardrails
- Static messages only: free, but history becomes unreadable

**Consequences**:
- Pro: Deterministic flow; `Completer` is one mocked method in tests
- Pro: The commit path itself runs only `add`/`commit`/`fetch`/`rebase`/`push`; destructive operations the agent might attempt via bash are caught separately by the guardrail (below)
- Con: One commit per session, no per-topic grouping
- Con: Message quality is bounded by what a diffstat conveys

### Result enums instead of exceptions for sync outcomes

**Choice**: `smartPush`/`smartPull` catch everything and return values from the `PUSH_RESULT`/`SYNC_RESULT` const maps; `PUSH_SUCCESS` names the acceptable push outcomes.
**Why**: Callers (the processor, the projects processor, startup sync) must always continue — an exception would abort a session close or startup over a network blip. Enumerated outcomes make every degraded path an explicit, logged branch.
**Consequences**:
- Pro: Callers cannot forget the failure path; tests assert exact outcomes
- Pro: The `PUSH_RESULT`/`SYNC_RESULT` constants give callers a closed, exhaustive outcome set
- Con: Failure detail lives in logs, not in the return value

### Agent-assisted conflict resolution, with abort as the fallback

**Choice**: Divergence is handled by `rebase --autostash`. A clean rebase pushes/continues as before. When the rebase conflicts and a `RebaseResolver` is supplied, `sync.ts` leaves the rebase *in progress* and hands it to the resolver — a headless side agent (`createGitResolver`, `resolve.ts`) that reads the conflicted files, writes merged content, stages, and runs `git rebase --continue` through cwd-scoped custom tools. The resolver is invoked in a bounded loop (`MAX_RESOLVER_ATTEMPTS = 3`), and **success is decided by `sync.ts` from the on-disk rebase state after each pass, never from the agent's own report**. A resolved conflict surfaces as `AGENT_RESOLVED` (added to `PUSH_RESULT`/`SYNC_RESULT` and to `PUSH_SUCCESS`); an unresolved one — or any path with no resolver wired — falls back to the original behavior: `git rebase --abort`, restoring a clean tree with local commits intact, surfacing as `REBASE_FAILED`/`SYNC_FAILED`. Both helpers also abort any stale rebase left by a crash before starting.
**Why**: Genuinely conflicting machines would otherwise stay diverged until a human intervened, which for an unattended workspace means silent drift. An LLM is the only thing that can merge conflicting edits without a human, and the rebase path is exactly where that judgment is needed. The risk of letting an LLM loose on the most delicate git path is contained by three guards: the resolution loop is bounded (no infinite spin against a misbehaving agent — the recurring OOM failure mode this project has hit before), the agent's tools are scoped to one repo and forbid push/fetch/reset/remote, and authority over "did it work" stays with deterministic filesystem state rather than the agent's claim. The resolver is an *optional* parameter, so callers that don't wire one (and the shared [projects](projects.md) primitives) keep the pure abort-on-conflict behavior unchanged.
**Alternatives Considered**:
- Abort always, no automated resolution: simplest and fully deterministic, but leaves real conflicts unresolved forever on an unattended remote
- Driving the rebase loop inside the agent (agent owns "continue until done"): rejected — trusting the agent's self-report for completion and giving it an unbounded loop is exactly the failure mode to avoid; the bound and the success check belong in `sync.ts`
**Consequences**:
- Pro: Conflicting machines self-heal when an agent can merge the edits; the bounded loop and clean-state fallback mean no half-finished rebase survives, covered by `tests/git/sync.test.ts` and `tests/git/resolve.test.ts`
- Pro: `AGENT_RESOLVED` lets callers/telemetry distinguish agent-resolved syncs from clean rebases
- Con: Adds an LLM dependency and cost to the conflict path; resolution quality is bounded by the agent, and a bad merge is committed (though it lands as ordinary local commits the next sync can still rework)
- Con: The side session runs at the workspace root, so the resolver relies on cwd-scoped custom tools rather than pi's built-in file/bash tools to act on a non-workspace repo correctly

### `HEAD` and gitlink resolution hardening

**Choice**: `resolveBranch` maps `"HEAD"` to the actual branch name via `symbolic-ref` before building remote refs; `resolveGitDir` follows `gitdir:` files when checking for rebase state.
**Why**: Repos created via `git init` (the workspace on first run) never get a `refs/remotes/origin/HEAD` ref, so passing `HEAD` through to `origin/HEAD` breaks the rebase ref and false-positives divergence checks into `DIVERGED`. Submodules store `.git` as a file, which would make `rebase-merge`/`rebase-apply` detection silently wrong for every project repo.
**Consequences**:
- Pro: Callers can pass `"HEAD"` everywhere; one code path serves workspace and submodules
- Con: Two subtle resolution steps that must be preserved when touching `sync.ts`

### History rewrites funneled through one explicit `scrub` tool

**Choice**: The only sanctioned history rewrite is the `scrub` tool wrapping `git filter-repo --invert-paths`. It refuses on a dirty tree, validates each path exists in history, probes for `git filter-repo` before running, restores the (filter-repo-stripped) `origin` remote and force-pushes, and reports every outcome via the `SCRUB_RESULT` enum instead of throwing. The tool targets the workspace repo by default; an optional `project` name retargets the rewrite to `projects/<name>`, resolved in the tool layer (the generic `scrubPaths` primitive is repo-agnostic, and the git extension does not import the projects extension — the dependency runs projects → git). An empty or non-existent project name throws before any git operation. Because `filter-repo` rewrites the project's commit SHAs, a successful project scrub notes that the workspace's submodule pointer updates at the next session-close commit.
**Why**: Purging a leaked secret or a large blob is a real, occasionally necessary operation, but it is destructive and irreversible — it belongs behind a single, guarded, clearly-labeled tool rather than left to ad-hoc bash. The same result-enum discipline as the sync helpers keeps it from aborting a session.
**Alternatives Considered**:
- Let the agent run `git filter-repo` via bash: rejected — no clean-tree/path/tool-availability guards, and the guardrail blocks it anyway
- A native blob filter instead of `git filter-repo`: more code and easy to get subtly wrong; `git filter-repo` is the well-tested standard, and a missing-tool path covers environments without it
**Consequences**:
- Pro: One audited path for the most dangerous git operation; graceful, explicit failures (dirty tree, unknown path, tool missing, push failed)
- Con: Depends on `git filter-repo` being installed; absent it, scrub is unavailable (reported clearly rather than failing opaquely)

### Destructive-git guardrail via the pi `tool_call` event

**Choice**: A second extension factory registers a `pi.on("tool_call")` handler that, for `bash` calls, splits the command on shell operators (quoting-aware) and blocks the whole call when any sub-command matches `DESTRUCTIVE_GIT_DENY_PATTERNS` (`git push`, `reset`, `checkout/restore .`, `clean`, mutating `remote`, `filter-repo`, `rebase`), returning `{ block, reason }` that names the dedicated tools.
**Why**: pi's `tool_call` event is the documented, supported pre-execution interception point and can block (`docs/reference/pi-sdk-notes.md`). Gating here means destructive history mutation only ever flows through the recoverable tools (commit/scrub/automatic sync), never through raw bash, even when the agent improvises a command. Compound-splitting with quote awareness stops a destructive sub-command from hiding behind `&&`/`|`/`;` or inside a quoted argument.
**Mechanism & limitation**: The guard binds to the `bash` tool that pi exposes in the main agent session; it covers the agent's own bash path, which is the threat. It does **not** intercept git that the extension itself runs through `execFile` (`runGit`/`smartPush`/`scrub`) — those bypass the tool layer by design and remain allowed. It also does not police bash run by other surfaces that don't load this extension's factory (e.g. side/background sessions that build their own tool set); the guardrail must be wired into any session whose bash should be gated. `git rebase` is in the deny list for the agent's bash, while `sync.ts` still rebases freely because it never goes through the tool path.
**Consequences**:
- Pro: Uses a first-class pi hook — no monkey-patching of the bash tool; blocking is surfaced to the agent with actionable guidance
- Pro: Pure, exported helpers (`splitCompoundCommands`, `findDeniedSubcommand`) are fully unit-tested without a live session
- Con: Enforcement is per-session-wiring, not global — a session that omits the factory is ungated; pattern-based matching is a denylist, so genuinely novel destructive invocations could slip through and the list must be maintained

## System Behavior

### Scenario: Two machines share one workspace remote

**Given**: Machine A pushed workspace commits; machine B closed a session with its own commits
**When**: B's `git-commit` processor pushes
**Then**: `smartPush` fetches, classifies `DIVERGED`, rebases B's commits onto the remote, and pushes — history stays linear with no merge commits.

### Scenario: The divergence conflicts and the agent resolves it

**Given**: Both machines edited the same file region
**When**: The rebase hits a conflict and a resolver is wired
**Then**: The rebase is left in progress and handed to the side agent, which merges the conflicted files, stages them, and continues the rebase; `sync.ts` confirms the rebase finished from disk state, B's commits are pushed, and the result is `AGENT_RESOLVED`.

### Scenario: The divergence conflicts and the agent cannot resolve it

**Given**: The same conflict, but the agent cannot produce a clean merge within the bounded attempts (or no resolver is wired)
**When**: The resolution loop exhausts its attempts
**Then**: The rebase is aborted, B's working tree is clean, its commits remain local, the result `REBASE_FAILED`/`SYNC_FAILED` is logged as a warning, and the session close completes normally; the next startup sync or push retries.

### Scenario: Startup with a dirty workspace

**Given**: A previous run crashed before its commit pass
**When**: `initializeWorkspaceRepo` reaches the sync step
**Then**: `smartPull` returns `DIRTY_SKIPPED` with a warning — nothing is fetched or rebased over uncommitted work; the changes are committed at the next session close.

## Notes

- Tests (`tests/git/`) run against real git repos in temp directories; `setupRemotePair` builds a bare origin with two clones to produce ahead/behind/diverged topologies — no LLM or network beyond the filesystem. Conflict-resolution tests inject a fake `RebaseResolver` (one that drives the rebase to completion, and ones that leave it stuck) so the `AGENT_RESOLVED`/abort branches and the bounded loop are covered without an LLM; `resolve.test.ts` mocks `side.run` to assert the resolver wires cwd-scoped tools and that the `git` tool refuses push/fetch/reset/remote. `scrub.test.ts` runs the real `git filter-repo` (the rewrite cases are skipped when the tool is absent); `guardrail.test.ts` is pure-function deny-pattern and compound-split coverage plus a faked `pi.on` to assert the `block`/`reason` shape
- `commit_workspace` exists for "save this now" requests; the description steers the agent away from it since session close commits anyway
- The fixed identity and `commit.gpgsign false` are repo-local, so the user's global git configuration is never consulted or modified
