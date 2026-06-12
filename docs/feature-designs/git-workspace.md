# Design: Workspace Git Versioning

<!-- This design describes the current implementation approach. Updated through delta reconciliation. -->

**Feature Spec**: [../feature-specs/git-workspace.md](../feature-specs/git-workspace.md)
**Status**: Current

## Purpose

This document explains how the workspace stays version-tracked unattended: why commits are a single staged pass with an LLM-generated subject line, why sync helpers return result enums instead of throwing, and how the same primitives serve both the workspace and registered projects.

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

The extension (`src/extensions/git/index.ts`) wires a bootstrap hook, a pi tools factory, and a finalize-phase post-processor. Underneath sit three layered modules: `git.ts` (promisified `execFile` subprocess primitives that never shell out), `commit.ts` (stage-all + message generation), and `sync.ts` (divergence classification and the push/pull state machines). Commit and sync take a `cwd`, so the projects extension reuses them verbatim against each submodule.

```
session close ─► git-commit (finalize)
                   ├─ commitAll: add -A → diffstat → side.complete → commit
                   └─ smartPush: abort stale rebase → fetch → detectDivergence
                        ├─ AHEAD     → push
                        ├─ DIVERGED  → rebase --autostash → push | abort → REBASE_FAILED
                        └─ UP_TO_DATE/BEHIND → NOTHING_TO_PUSH
```

## Components

### Implementation Structure

| Component | Responsibility | Key Decisions |
|-----------|----------------|---------------|
| `src/extensions/git/index.ts` | `defineExtension` wiring; honors `enabled` flag | Hook + tools factory + processor; no logic |
| `src/extensions/git/git.ts` | Subprocess primitives: `runGit` (throws), `runGitCapture` (never throws), `hasUncommittedChanges`, `hasRemote` | `execFile`-based, no shell strings (DES-002); capture variant returns `{code, stdout, stderr}` so callers branch on exit codes |
| `src/extensions/git/commit.ts` | `commitAll`: stage everything, generate message from diffstat, commit | `Completer = Pick<SideRunner, "complete">` for fakeable tests; sanitization + 100-char cap; explicit `message` skips generation |
| `src/extensions/git/sync.ts` | `detectDivergence`, `smartPush`, `smartPull`, result const maps | merge-base ancestor checks; stale-rebase abort on entry; gitlink and `HEAD`-name resolution; never throws |
| `src/extensions/git/hooks.ts` | `initializeWorkspaceRepo`: init + identity + gitignore + startup `smartPull` | Fixed identity, `commit.gpgsign false`; gitignore entries appended uncommitted on every startup |
| `src/extensions/git/processor.ts` | `git-commit` post-processor (`finalize`) | Commit → push when `origin` exists → verify clean → one retry commit |
| `src/extensions/git/tools.ts` | `query_git_status`, `list_recent_commits`, `commit_workspace` | Handlers exported standalone; outputs truncated with pi's `truncateTail` |

## Key Decisions

### Single diffstat completion instead of a commit agent

**Choice**: One `git add -A`, one `side.complete` call over the staged diffstat producing a single subject line, one commit.
**Why**: The Python implementation spawned a Haiku agent with bash access to group changes into multiple commits. A plain completion is cheaper, faster, fully fakeable in tests, and exposes no tool surface to constrain — and a per-session bulk commit is granular enough for a workspace whose history exists for review and rollback.
**Alternatives Considered**:
- Headless agent run (`app.agent.side.run`) grouping commits: better commit granularity, but slower, costlier, and needs git-command guardrails
- Static messages only: free, but history becomes unreadable

**Consequences**:
- Pro: Deterministic flow; `Completer` is one mocked method in tests
- Pro: No destructive-command risk — the extension itself runs only `add`/`commit`/`fetch`/`rebase`/`push`
- Con: One commit per session, no per-topic grouping
- Con: Message quality is bounded by what a diffstat conveys

### Result enums instead of exceptions for sync outcomes

**Choice**: `smartPush`/`smartPull` catch everything and return values from the `PUSH_RESULT`/`SYNC_RESULT` const maps; `PUSH_SUCCESS` names the acceptable push outcomes.
**Why**: Callers (the processor, the projects processor, startup sync) must always continue — an exception would abort a session close or startup over a network blip. Enumerated outcomes make every degraded path an explicit, logged branch.
**Consequences**:
- Pro: Callers cannot forget the failure path; tests assert exact outcomes
- Pro: Matches the Python `PUSH_RESULT`/`SYNC_RESULT` contract, easing the rewrite
- Con: Failure detail lives in logs, not in the return value

### Abort-on-conflict recovery, no automated resolution

**Choice**: Divergence is handled by `rebase --autostash`; a conflicting rebase is immediately aborted, restoring a clean tree, and surfaces as `REBASE_FAILED`/`SYNC_FAILED`. Both helpers also abort any stale rebase left by a crash before starting.
**Why**: Local commits plus a clean tree is always a recoverable state — the next sync retries. Automated conflict resolution (the Python version spawned an agent for it) adds an LLM dependency to the most delicate git path; it is deliberately out of scope here until proven necessary.
**Consequences**:
- Pro: No half-finished rebases can survive a crash; behavior is fully covered by `tests/git/sync.test.ts`
- Con: Genuinely conflicting machines stay diverged until a human (or the agent, via conversation) intervenes

### `HEAD` and gitlink resolution hardening

**Choice**: `resolveBranch` maps `"HEAD"` to the actual branch name via `symbolic-ref` before building remote refs; `resolveGitDir` follows `gitdir:` files when checking for rebase state.
**Why**: Repos created via `git init` (the workspace on first run) never get a `refs/remotes/origin/HEAD` ref, so passing `HEAD` through to `origin/HEAD` breaks the rebase ref and false-positives divergence checks into `DIVERGED`. Submodules store `.git` as a file, which would make `rebase-merge`/`rebase-apply` detection silently wrong for every project repo.
**Consequences**:
- Pro: Callers can pass `"HEAD"` everywhere; one code path serves workspace and submodules
- Con: Two subtle resolution steps that must be preserved when touching `sync.ts`

## System Behavior

### Scenario: Two machines share one workspace remote

**Given**: Machine A pushed workspace commits; machine B closed a session with its own commits
**When**: B's `git-commit` processor pushes
**Then**: `smartPush` fetches, classifies `DIVERGED`, rebases B's commits onto the remote, and pushes — history stays linear with no merge commits.

### Scenario: The divergence conflicts

**Given**: Both machines edited the same file region
**When**: The rebase hits a conflict
**Then**: The rebase is aborted, B's working tree is clean, its commits remain local, the result `REBASE_FAILED` is logged as a warning, and the session close completes normally; the next startup sync or push retries.

### Scenario: Startup with a dirty workspace

**Given**: A previous run crashed before its commit pass
**When**: `initializeWorkspaceRepo` reaches the sync step
**Then**: `smartPull` returns `DIRTY_SKIPPED` with a warning — nothing is fetched or rebased over uncommitted work; the changes are committed at the next session close.

## Notes

- Tests (`tests/git/`) run against real git repos in temp directories; `setupRemotePair` builds a bare origin with two clones to produce ahead/behind/diverged topologies — no LLM or network beyond the filesystem
- `commit_workspace` exists for "save this now" requests; the description steers the agent away from it since session close commits anyway
- The fixed identity and `commit.gpgsign false` are repo-local, so the user's global git configuration is never consulted or modified
