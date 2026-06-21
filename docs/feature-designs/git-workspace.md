# Design: Workspace Git Versioning

<!-- This design describes the current implementation approach. Updated through delta reconciliation. -->

**Feature Spec**: [../feature-specs/git-workspace.md](../feature-specs/git-workspace.md)
**Status**: Current

## Purpose

This document explains how the workspace stays version-tracked unattended: why commits are an agent-driven grouped pass with a deterministic fallback, why sync helpers return result enums instead of throwing, how the same primitives serve both the workspace and registered projects, and why destructive history operations are funneled through one explicit tool while the agent's raw bash path is gated against them.

## Problem Context

Every session leaves the workspace mutated — memory extraction, context edits, project pointer bumps — and none of it should require manual git operations. The same workspace may be shared across machines through an `origin` remote, so a bare `git push` would eventually hit non-fast-forward rejections, and any conflict handling must work without a human present.

**Constraints:**
- Runs unattended at session close and at startup — no prompts, no GPG, no reliance on the user's global git config
- Post-processing must never fail the session close: every outcome degrades to a log line with local commits preserved
- Linear history on a single branch — divergence is resolved by rebasing local commits onto the remote, never by merge commits or force pushes
- The primitives must work in plain repos, submodules (gitlink `.git` files), and repos created via `git init` that lack an `origin/HEAD` ref

**Interactions:**
- The [projects](projects.md) extension consumes `commitAll`, `smartPull`, and `smartPush` through the `app.git` service (and builds its resolver with `createGitResolver` imported from core `src/git/`), running its processor in `preFinalize` so submodule pointer updates are already on disk when `git-commit` runs in `finalize`
- Post-processing phases and session close are orchestrated by the coordinator ([conversation-loop.md](conversation-loop.md)); memory extraction writes the files this processor commits ([memory.md](memory.md))
- Side completions go through `app.agent.side` at the `processor` tier ([DES-001](../design/DES-001-unified-extension-api.md))

## Design Overview

The extension (`src/extensions/git/index.ts`) wires a bootstrap hook, two pi extension factories (a tools factory and a bash guardrail factory), and a finalize-phase post-processor. The reusable git *primitives* are no longer part of this extension: they live in a neutral **core module** `src/git/` — `git.ts` (promisified `execFile` subprocess primitives that never shell out), `commit.ts` (stage-all + message generation), and `sync.ts` (divergence classification and the push/pull state machines, including the `RebaseResolver` type), and `resolve.ts` (`createGitResolver`, the agent-backed `RebaseResolver` implementation). The extension consumes them directly and also exposes the high-level operations (`commitAll`, `smartPush`, `smartPull`) to all extensions through the **`app.git`** service (see [DES-001](../design/DES-001-unified-extension-api.md)), so consumers like [projects](projects.md) and [memory](memory.md) no longer import the git extension. What remains under `src/extensions/git/` is purely extension-specific: `scrub.ts` (history rewrite via `git filter-repo`), `guardrail.ts` (compound-command splitting + the destructive deny patterns + the `tool_call` interceptor), `tools.ts`, `processor.ts`, and `hooks.ts`. Commit, sync, and scrub take a `cwd`, so the same primitives serve the workspace, registered projects, and the memory store.

```
session close ─► git-commit (finalize)
                   ├─ commitAll: commit agent (group → add+commit per group) → still dirty? add -A + commit fallback
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

Both persistence paths — the finalize pass above and the debounced mid-session fire — share one `commitAll → smartPush` flow; the debounce timer (`createDebouncedTask`, [DES-007](../design/DES-007-debounced-background-task.md)) simply arms that same flow to run after a configurable quiet window instead of at close.

## Components

### Implementation Structure

| Component | Responsibility | Key Decisions |
|-----------|----------------|---------------|
| `src/git/git.ts` *(core module)* | Subprocess primitives: `runGit` (throws), `runGitCapture` (never throws), `hasUncommittedChanges`, `hasRemote`, `listSubmodules` | `execFile`-based, no shell strings (DES-002); capture variant returns `{code, stdout, stderr}` so callers branch on exit codes. Core utility — any extension may import it directly for low-level ops |
| `src/git/commit.ts` *(core module)* | `commitAll`: run the commit agent, then fall back to one staged commit on failure | Agent-first with a deterministic fallback; subjects read from git (disk-trust, not the agent's report); returns `string[]`. Exposed to extensions via `app.git.commitAll` |
| `src/git/commit-agent.ts` *(core module)* | `CommitAgent` type, `createCommitAgent`: a headless side agent that groups changes into cohesive commits via cwd-scoped tools | Two modes — `"workspace"` groups by area, `"project"` matches the repo's own commit style (`git log` + CONTRIBUTING/CLAUDE/AGENTS); the `git` tool is an allowlist (`status`/`diff`/`log`/`add`/`commit`/`show`) so the agent can only add commits; commits run with `core.editor=true`/`commit.gpgsign=false`. `createCommitAgent` exposed via `app.git`; the git extension imports it directly for workspace mode |
| `src/git/sync.ts` *(core module)* | `detectDivergence`, `smartPush`, `smartPull`, the `RebaseResolver` type, result const maps | merge-base ancestor checks; stale-rebase abort on entry; gitlink and `HEAD`-name resolution; optional `RebaseResolver` for conflicts with a bounded attempt loop; never throws. `smartPush`/`smartPull` exposed via `app.git` |
| `src/git/resolve.ts` *(core module)* | `createGitResolver`: the agent-backed `RebaseResolver` (type from `src/git/sync.ts`), with cwd-scoped `read_conflict`/`write_resolved`/`git` custom tools | One side-agent pass per call; tools bound to the target repo so the agent can't touch the wrong tree; `git` tool rejects push/fetch/reset/remote/filter-repo; swallows agent errors so a sync is never aborted by a throw |
| `src/extensions/git/index.ts` | `defineExtension` wiring; honors `enabled` flag | Hook + tools factory + processor; no logic |
| `src/extensions/git/hooks.ts` | `initializeWorkspaceRepo`: init + identity + gitignore + startup `smartPull` | Fixed identity, `commit.gpgsign false`; gitignore entries appended uncommitted on every startup |
| `src/extensions/git/processor.ts` | `commitAndPushWorkspace` (shared commit → push when `origin` exists → verify clean → one retry commit) and the `git-commit` `finalize` post-processor that wraps it | Finalize clears and drains the debouncer first, then runs the shared commit-push; the same `commitAndPushWorkspace` is the debounced mid-session fire |
| `src/extensions/git/exchange.ts` | `git-exchange-signal` exchange processor | Each exchange only resets the debounce timer; nothing commits on the exchange path |
| `src/util/debouncer.ts` *(shared utility)* | `createDebouncedTask`: trailing-edge debounce over an async task | Resettable `unref`-ed timer, single-flight with coalescing, `clear`/`whenIdle`, disabled when `delay≤0` ([DES-007](../design/DES-007-debounced-background-task.md)) |
| `src/extensions/git/tools.ts` | `query_git_status`, `list_recent_commits`, `commit_workspace`, `scrub` | Handlers exported standalone; outputs truncated with pi's `truncateTail`; `commit_workspace` commits then pushes the workspace and every ahead project submodule via `smartPush` (resolver wired), reporting a per-repo outcome line; scrub handler resolves the target repo (workspace, or `projects/<name>` when `project` is given), validates it is non-empty and exists, delegates to `scrubPaths`, and surfaces its outcome message (plus a stale submodule-pointer note on a successful project scrub) |
| `src/extensions/git/scrub.ts` | `scrubPaths`: clean-tree + path-existence + tool-availability checks, clears `git filter-repo`'s per-repo state, `git filter-repo --invert-paths`, origin restore + force-push | Returns a `SCRUB_RESULT` enum outcome, never throws; `isFilterRepoAvailable` probes `git filter-repo --version` so a missing tool is a clean error, not a crash; clears filter-repo's per-repo metadata before each rewrite so repeated scrubs stay non-interactive |
| `src/extensions/git/guardrail.ts` | `DESTRUCTIVE_GIT_DENY_PATTERNS`, `splitCompoundCommands`, `findDeniedSubcommand`, `createGitGuardrailFactory` | A `tool_call` interceptor that blocks the `bash` tool on destructive git; quoting-aware compound split so a destructive sub-command can't hide behind quotes or operators |

## Key Decisions

### Agent-driven grouped commits, with a deterministic fallback

**Choice**: `commitAll` runs a headless side agent first (`createCommitAgent`, `src/git/commit-agent.ts`) — the same `side.run` + cwd-scoped custom-tool shape as the rebase resolver — that inspects the diff, groups changes into cohesive sets, and creates one descriptive commit per group. The agent ALWAYS runs when there are changes; only if it throws, commits nothing, or leaves the tree dirty does `commitAll` fall back to one `git add -A` + `git commit -m <fallbackMessage>` with a deterministic dated message. The subjects returned are read from git (`git log head..HEAD`), never from the agent's self-report, so partial commits survive an agent failure.
**Why**: A workspace session touches several unrelated areas at once (episodic memories, topic notes, context files, config), and a single diffstat summary lumps them into one undifferentiated commit that is hard to review or revert. Grouping mirrors what a person would do and matches the legacy behavior. The agent's tools are an allowlist (`status`/`diff`/`log`/`add`/`commit`/`show`) bound to the target repo, so it can only add commits — never push, reset, rebase, or rewrite — making a full agent safe on the commit path. The deterministic fallback guarantees a session close never leaves changes uncommitted even if the model is down.
**Alternatives Considered**:
- Single diffstat completion (`side.complete` over `git diff --cached --stat`): cheaper and faster, but one commit per session with a message bounded by what a diffstat conveys
- Static messages only: free, but history becomes unreadable

**Consequences**:
- Pro: Per-topic grouping; descriptive, area-specific messages; project commits match each project's own style
- Pro: Both the agent path and the fallback leave the tree clean; subjects are read from git so a partial agent run is still reported accurately
- Con: A `processor`-tier agent run per session close (and per dirty project) is costlier and slower than a one-shot completion; the deterministic fallback message is generic when the agent fails
- Con: The commit agent's `git`-tool allowlist must be maintained alongside the agent bash guardrail's denylist

### Result enums instead of exceptions for sync outcomes

**Choice**: `smartPush`/`smartPull` catch everything and return values from the `PUSH_RESULT`/`SYNC_RESULT` const maps; `PUSH_SUCCESS` names the acceptable push outcomes.
**Why**: Callers (the processor, the projects processor, startup sync) must always continue — an exception would abort a session close or startup over a network blip. Enumerated outcomes make every degraded path an explicit, logged branch.
**Consequences**:
- Pro: Callers cannot forget the failure path; tests assert exact outcomes
- Pro: The `PUSH_RESULT`/`SYNC_RESULT` constants give callers a closed, exhaustive outcome set
- Con: Failure detail lives in logs, not in the return value

### Agent-assisted conflict resolution, with abort as the fallback

**Choice**: Divergence is handled by `rebase --autostash`. A clean rebase pushes/continues as before. When the rebase conflicts and a `RebaseResolver` is supplied, `sync.ts` leaves the rebase *in progress* and hands it to the resolver — a headless side agent (`createGitResolver`, `src/git/resolve.ts`) that reads the conflicted files, writes merged content, stages, and runs `git rebase --continue` through cwd-scoped custom tools. The resolver is invoked in a bounded loop (`MAX_RESOLVER_ATTEMPTS = 3`), and **success is decided by `sync.ts` from the on-disk rebase state after each pass, never from the agent's own report**. A resolved conflict surfaces as `AGENT_RESOLVED` (added to `PUSH_RESULT`/`SYNC_RESULT` and to `PUSH_SUCCESS`); an unresolved one — or any path with no resolver wired — falls back to the original behavior: `git rebase --abort`, restoring a clean tree with local commits intact, surfacing as `REBASE_FAILED`/`SYNC_FAILED`. Both helpers also abort any stale rebase left by a crash before starting.
**Why**: Genuinely conflicting machines would otherwise stay diverged until a human intervened, which for an unattended workspace means silent drift. An LLM is the only thing that can merge conflicting edits without a human, and the rebase path is exactly where that judgment is needed. The risk of letting an LLM loose on the most delicate git path is contained by three guards: the resolution loop is bounded (no infinite spin against a misbehaving agent — the recurring OOM failure mode this project has hit before), the agent's tools are scoped to one repo and forbid push/fetch/reset/remote, and authority over "did it work" stays with deterministic filesystem state rather than the agent's claim. The resolver is an *optional* parameter, so callers that don't wire one keep the pure abort-on-conflict behavior unchanged. The [projects](projects.md) extension wires a resolver (built with `createGitResolver` from core `src/git/`) into its startup `smartPull` and session-close `smartPush`, so its submodule syncs participate in agent-assisted resolution just like the workspace paths.
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

**Choice**: The only sanctioned history rewrite is the `scrub` tool wrapping `git filter-repo --invert-paths`. It refuses on a dirty tree, validates each path exists in history, probes for `git filter-repo` before running, restores the (filter-repo-stripped) `origin` remote and force-pushes, and reports every outcome via the `SCRUB_RESULT` enum instead of throwing. Before each rewrite it clears `git filter-repo`'s per-repo metadata, so a repeated scrub stays non-interactive — `filter-repo` otherwise prompts on stdin when its `already_ran` marker is older than a day, which hangs the tool's non-interactive invocation (`--force` does not bypass that prompt, only the separate fresh-clone check). The tool targets the workspace repo by default; an optional `project` name retargets the rewrite to `projects/<name>`, resolved in the tool layer (the generic `scrubPaths` primitive is repo-agnostic, and the git extension does not import the projects extension — the dependency runs projects → git). An empty or non-existent project name throws before any git operation. Because `filter-repo` rewrites the project's commit SHAs, a successful project scrub notes that the workspace's submodule pointer updates at the next session-close commit.
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

### Debounced mid-session commit-push

**Choice**: Rather than commit on every exchange, each exchange resets a trailing-edge debounce timer (`createDebouncedTask`, [DES-007](../design/DES-007-debounced-background-task.md)); after `[scheduler] commitDebounceMinutes` (default 5, `0` disables) of exchange quiet the workspace is committed and pushed in the background via the same `commitAndPushWorkspace` the close pass uses. The finalize pass clears and drains the debouncer first so it owns persistence exclusively and never races a fire; shutdown clears the pending timer (the drain's finalize handles persistence).
**Why**: Committing (an agent model call) and pushing on every exchange is wasteful and would extend every exchange with network I/O. Deferring to one fire after a quiet burst batches the work and runs it during idle — when no exchange is streaming, so there is no partial-write risk mid-exchange. The accepted tradeoff is that an active conversation (exchanges within the window) defers all persistence until the window elapses after the last exchange: a mid-conversation crash loses only that burst's uncommitted work, recovered at the next fire or session close. This is the cost of also eliminating the per-exchange agent model call.
**Alternatives Considered**:
- Debounce the push only, keep an immediate per-exchange commit: preserves on-disk durability but keeps paying the agent model call every exchange
- A single shared timer across workspace and projects: would need a host-owned service, breaking the projects→git decoupling invariant
**Consequences**:
- Pro: One agent commit + one push per quiet burst instead of per exchange; fires during idle so no exchange is streaming when the commit runs
- Pro: Finalize and the debounce fire share one `commitAndPushWorkspace`, so both follow identical commit-then-push semantics
- Con: Up to one debounce window of uncommitted work is at risk during a crash in an active conversation; `0` reverts to close-only persistence
- Con: One debouncer per extension (the workspace here; [projects](projects.md) has its own), so a workspace fire can transiently commit a submodule pointer one window stale — self-healing on the next fire and guaranteed correct by the close pass's phase ordering (projects `preFinalize` before workspace `finalize`)

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
- `commit_workspace` exists for "save and publish this now" requests: it commits then pushes the workspace and any ahead project submodules (clean-but-ahead included) via `smartPush`, reporting a per-repo outcome line and accepting `push=false` to commit only; the description steers the agent away from it for routine work since session close commits and pushes anyway
- The fixed identity and `commit.gpgsign false` are repo-local, so the user's global git configuration is never consulted or modified
