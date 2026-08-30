# DES-010: Agent-Driven Git Through a Scoped Custom Tool, Verified by the Host from Git State

**Scope**: Project-wide
**Date**: 2026-08-30
**Last Updated**: 2026-08-30

## Pattern

When an agent must perform git work — grouping changes into commits, resolving a rebase conflict, authoring a proposal branch — it drives git through a **purpose-built in-process `git` tool** bound to one target working tree, with a per-consumer allowlist of subcommands (and, where the operation is dangerous, argument-level guards). It does not use bash. After the run, the **host decides outcomes from git state** — `git log` against a pre-captured HEAD, rebase progress on disk, `ls-remote` for remote truth — never from the agent's self-report.

Two framings converge on the same rule:

- In any **bash-granted session** (main or background — the guardrail factory is bound to both scopes), the bash guardrail (`src/extensions/git/guardrail.ts`) blocks destructive git commands (push, reset, rebase, clean, remote mutations, filter-repo) at the tool layer; git work that needs those operations gets a dedicated tool whose surface is enumerated, not bash with prompt-level rules.
- In a **headless run** (`side.run` with an empty built-in allowlist), bash is not granted at all — the custom `git` tool is what grants git access, in exactly the enumerated form.

One shared implementation: `src/git/agent-tools.ts` — the `AgentRunner` slice, the shared stage-and-commit subcommand set (`status`/`diff`/`log`/`show`/`add`/`commit`, commits run with `core.editor=true` and per-consumer config such as `commit.gpgsign=false`), truncated result shaping (`textResult`), and instructive refusals. A new consumer extends it rather than forking the tooling.

**Refusals are instructive, so the agent self-corrects within the run**: a refused subcommand returns an error naming the rule (e.g. "branch name must match `skill-evolution/…`", "refusing to push over an existing remote branch"), letting the agent retry correctly — the collision-suffix case resolves itself without host intervention.

**Verification is host-owned and varies by consumer**:

- Commit agent: capture `rev-parse HEAD` before the run; afterwards read the new subjects with `git log`; fall back to a deterministic commit if the agent left the tree dirty.
- Rebase resolver: re-check `rebaseInProgress` on disk after every pass, under a bounded attempt loop.
- Proposal agent: scan `ls-remote` for the feature-owned ref namespace, read tip SHAs from it, and check the pushed diff's scope — the agent's report can at worst cause an unlogged candidate, never a wrong ledger entry.

## Rationale

The agent is better than the host at *judgment* (what belongs in a commit, how to resolve a hunk, how to word a change) and worse at *accounting* — it can misreport a SHA, forget a step, or claim work that failed. So the split is: agent drives (scoped, enumerated, self-correcting), host verifies (deterministic, from state that cannot lie). This is the git-shaped instance of deriving outcomes from observable state ([DES-006](DES-006-state-based-migration-detection.md)); the tool-grant mechanics follow [ADR-015](../architecture/ADR-015-subagent-extension-tool-grants.md).

## Examples

### Do This

```
// one shared allowlist; argument guards where the op is dangerous
if (!ALLOWED_GIT_SUBCOMMANDS.has(subcommand)) return refusal(`git ${subcommand} is not allowed here`)
if (op === "push") assertNamePattern(name) && assertNotDefault(name) && assertRemoteAbsent(name)

// host verifies after the run — never the agent's report
const before = await revParseHead(cwd)
await side.run(...)                       // agent groups and commits
const subjects = await subjectsSince(cwd, before)   // disk-trust
```

**Why**: The allowlist is the enforcement — the agent's only git surface is the enumerated one, and the host's bookkeeping keys on what git reports, not what the agent claims. A refusal that names the rule lets the agent retry correctly within the same run.

### Don't Do This

```
// prompt-level restrictions are not enforcement
"you may push, but only to skill-evolution/* branches"        // ❌ grant bash + rules

// agent self-report as bookkeeping
const { shas } = await side.run(...)                          // ❌ ledger written from the report
logProposal(shas)

// per-consumer forks of the same tooling                       // ❌ fourth copy of the allowlist
```

**Why**: Prompt-level rules have no teeth (a model under pressure ignores them), an agent's self-report is unreliable accounting (misremembered SHAs, claimed-but-failed steps), and per-consumer forks drift — the fourth consumer extends `agent-tools.ts` rather than copying it.

## Exceptions

- The **rebase resolver** (`src/git/resolve.ts`) gates with a denylist (`FORBIDDEN_GIT_SUBCOMMANDS`: push, fetch, remote, filter-repo, reset) rather than an allowlist: it must legitimately drive a multi-step rebase (`rebase --continue`, `--abort`, `--skip`, inspection), so the safe set is not enumerable. The rule is allowlist-by-default; a denylist is acceptable only where the safe set cannot be enumerated, and the dangerous set must still be named exhaustively.

## Related

- See also: [DES-006](DES-006-state-based-migration-detection.md) — derive outcomes from observable state (this DES is its git-agent instance)
- See also: [ADR-015](../architecture/ADR-015-subagent-extension-tool-grants.md) — subagent tool-grant architecture
- Pattern rule: [DES-002](DES-002-extension-authoring.md) — the shared tooling lives in a neutral module (`src/git/`), not in any consumer's extension
- Related feature: [skill-evolution](../feature-designs/skill-evolution.md) — the proposal agent, the most fully guarded consumer (branch namespace, create-only pushes, path-validated worktrees)
- Implementation: `src/git/agent-tools.ts`; consumers `src/git/commit-agent.ts`, `src/git/resolve.ts`, `src/extensions/skill-evolution/propose.ts`
