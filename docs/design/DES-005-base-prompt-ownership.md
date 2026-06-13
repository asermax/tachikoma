# DES-005: Base Prompt Ownership

**Scope**: Project-wide
**Date**: 2026-06-13
**Last Updated**: 2026-06-13

## Pattern

A system prompt that **replaces pi's coding-agent base** — the standing identity for an execution
context (the main conversational session, the autonomous background-task agent, a delegated
subagent) — lives in the core module `src/agent/prompts.ts` and shares the single
`OPERATIONAL_GUIDANCE` block for role-agnostic working hygiene. The extension that runs the context
composes its prompt by calling a builder/constant from that module and installs it through the
DES-001 seam (`app.agent.systemPrompt(...)` for the main session, `side.run({ system })` for headless
runs) — it does not author base-prompt text inline.

A prompt that is **a discrete task handed to a side-run** (a classifier, extractor, summarizer, or
one-shot writer) is *not* a base prompt and stays co-located with the feature that owns it.

The deciding predicate: *does this prompt stand in for pi's coding-agent identity for a whole
execution context?* If yes → core `prompts.ts` + shared `OPERATIONAL_GUIDANCE`. If it is a
feature's own task instruction → inline in the feature.

## Rationale

Three execution contexts each need their own role-appropriate identity that fully replaces pi's
native coding-agent base (Tachikoma is a personal assistant, not a coding agent). They also share
real operational hygiene (conciseness, dedicated file tools over bash, parallel tool calls,
`file_path:line_number` citation, no over-engineering). Centralizing the base prompts and the shared
core in one module gives a single source for that hygiene and a single place a future execution
context goes for its identity — while reproducing the guidance in-source keeps a deployment from
depending on the operator's personal pi config (`~/.pi/agent/APPEND_SYSTEM.md`). See [ADR-001](../architecture/ADR-001-agent-sdk.md).

Feature-local task prompts are excluded deliberately: they are coupled to one feature's logic, never
frame a whole conversation, and would only add noise to the core module.

## Examples

### Do This

```ts
// src/agent/prompts.ts — base prompts + shared hygiene live here
export const OPERATIONAL_GUIDANCE = `- Be concise and direct.\n- ...`;
export const buildMainSystemPrompt = ({ soul, user, workspaceRoot }) => [...].join("\n\n");
export const SUBAGENT_SYSTEM_PROMPT = `You are a focused worker ...\n${OPERATIONAL_GUIDANCE}`;

// src/extensions/context/index.ts — the extension composes, it does not author base text
app.agent.systemPrompt(() => buildMainSystemPrompt({ soul, user, workspaceRoot }));
```

**Why**: The context's standing identity replaces pi's base, so it belongs in `prompts.ts`; the
extension is a consumer of the builder and shares the one `OPERATIONAL_GUIDANCE`.

### Don't Do This

```ts
// src/extensions/tasks/executor.ts — base prompt authored inline in the extension
const BACKGROUND_SYSTEM_PROMPT = `You are a background task agent. Be concise. Prefer ...`;
```

**Why**: This is a context's base identity, so it must not live inline — it drifts from the other
contexts and re-states the shared hygiene instead of reusing `OPERATIONAL_GUIDANCE`.

## Exceptions

Feature-local side-run task prompts stay inline with their feature — they are not base prompts:
`EVALUATOR_SYSTEM` (`tasks/executor.ts`), `SUMMARY_SYSTEM` (`boundary/summary.ts`), the episodic /
facts / preferences extraction prompts (`memory/extraction.ts`), and `COMMIT_MESSAGE_SYSTEM`
(`git/commit.ts`) all classify, extract, or write for one feature and never replace pi's base.

A delegated run additionally suppresses pi's append (`APPEND_SYSTEM.md`), project context files, and
the skills catalog via the `isolatePrompt` flag (see [agent-integration](../feature-designs/agent-integration.md)),
so the worker sees exactly its core-owned prompt — the mechanical expression of "don't inherit
operator config."

---

## Related

- See also: [DES-001](DES-001-unified-extension-api.md) — the `app.agent.systemPrompt` / `side.run` registration seam this pattern flows through
- See also: [ADR-001](../architecture/ADR-001-agent-sdk.md) — the always-replace-pi-base / don't-inherit-operator-append stance
- Related feature: [../feature-designs/foundational-context.md](../feature-designs/foundational-context.md) — the main-session identity
- Related feature: [../feature-designs/agent-integration.md](../feature-designs/agent-integration.md) — `isolatePrompt` and the side-run seam
