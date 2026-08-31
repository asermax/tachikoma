# DES-005: Base Prompt Ownership

**Scope**: Project-wide
**Date**: 2026-06-13
**Last Updated**: 2026-08-31

## Pattern

A system prompt that **replaces pi's coding-agent base** — the standing identity for an execution
context (the main conversational session, the autonomous background-task agent, a delegated
subagent) — lives in the core module `src/agent/prompts.ts` and shares the single
`OPERATIONAL_GUIDANCE` block for role-agnostic working hygiene. The **core installs it**, not an
extension: `AgentManager` applies `buildMainSystemPrompt({ workspaceRoot })` as the
`systemPromptOverride` for any non-bare session that brings no explicit prompt (the main session and
forks); headless/background runs pass their own via `side.run({ system })`. The main base prompt holds
the identity, the shared `OPERATIONAL_GUIDANCE` hygiene, and the workspace root, plus — main context
only — the conversation-substrate mechanics the coordinator owns (mid-exchange steering, `/queue`,
system-origin turns, delivery timing) and pointers to the core reference files in
`src/agent/references/`. It does **not** include the user-editable SOUL.md/USER.md content.

A prompt that is **a discrete task handed to a side-run** (a classifier, extractor, summarizer, or
one-shot writer) is *not* a base prompt and stays co-located with the feature that owns it.

**Per-extension context** (a subsystem's usage guidance, or user-editable identity content like
SOUL.md/USER.md) is *not* the core base prompt: it supplements the standing identity rather than
replacing pi's base. Each extension **owns its own text** (e.g. `src/extensions/<name>/usage.ts`, or
the `context` extension's SOUL/USER files) and contributes it through the DES-001 seam
`app.agent.use(provideContext(provide, customType?), { sessionScopes })`. The shared helper
(`provideContext`, `src/agent/system-prompt-section.ts`) turns that text into a `before_agent_start`
factory with two delivery modes: **with a `customType`** the content is injected as a hidden message
(subsystem usage guidance, volatile state like memory indexes / project state); **with no
`customType`** it is appended to the turn's system prompt (the `context` extension's SOUL/USER, which
layer the persona onto the core base prompt). The helper lives in core, but the **text stays with the
feature**. Scope each section to the sessions where it belongs (`["main", "background"]` vs main-only)
— never describe a tool to an agent that cannot call it.

The deciding predicate: *does this prompt stand in for pi's coding-agent identity for a whole
execution context?* If yes → core `prompts.ts` + shared `OPERATIONAL_GUIDANCE`. If it is a
feature's own task instruction → inline in the feature. That predicate decides **where a prompt
lives**. A second, content-level predicate governs **what the main base prompt may say**: it
documents only the conversation substrate the core owns and never names an extension's tool or turn
format — feature guidance belongs to the owning extension's usage section
([DES-014](DES-014-two-tier-agent-facing-documentation.md)).

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
export const buildMainSystemPrompt = ({ workspaceRoot }) => [...].join("\n\n"); // date + identity + hygiene + substrate mechanics + core reference pointers + root
export const SUBAGENT_SYSTEM_PROMPT = `You are a focused worker ...\n${OPERATIONAL_GUIDANCE}`;

// src/agent/manager.ts — the CORE installs the base prompt
systemPromptOverride = options.systemPrompt ?? (!bare ? buildMainSystemPrompt({ workspaceRoot }) : undefined);

// src/extensions/context/index.ts — SOUL/USER are user-editable supplements, appended on top
app.agent.use(provideContext(() => fresh("SOUL.md")));
app.agent.use(provideContext(() => fresh("USER.md")));
```

**Why**: The standing identity replaces pi's base, so it belongs in `prompts.ts` and is installed by
the core; SOUL/USER are user-editable workspace content layered on top via `provideContext`, sharing
the one `OPERATIONAL_GUIDANCE` through the core base prompt rather than re-stating it.

### Don't Do This

```ts
// src/extensions/tasks/executor.ts — base prompt authored inline in the extension
const BACKGROUND_SYSTEM_PROMPT = `You are a background task agent. Be concise. Prefer ...`;
```

**Why**: This is a context's base identity, so it must not live inline — it drifts from the other
contexts and re-states the shared hygiene instead of reusing `OPERATIONAL_GUIDANCE`.

## Exceptions

Feature-local side-run task prompts stay inline with their feature — they are not base prompts:
`EVALUATOR_SYSTEM` (`tasks/executor.ts`), `SUMMARY_SYSTEM` (`boundary/summary.ts`), the episodic / topics extraction prompts (`memory/extraction.ts`), and `COMMIT_MESSAGE_SYSTEM`
(`git/commit.ts`) all classify, extract, or write for one feature and never replace pi's base.

A delegated run additionally suppresses pi's append (`APPEND_SYSTEM.md`), project context files, and
the skills catalog via the `isolatePrompt` flag (see [agent-integration](../feature-designs/agent-integration.md)),
so the worker sees exactly its core-owned prompt — the mechanical expression of "don't inherit
operator config."

---

## Related

- See also: [DES-001](DES-001-unified-extension-api.md) — the `app.agent.use(provideContext(...))` / `side.run` registration seams this pattern flows through (the core installs the base prompt directly via `AgentManager`)
- See also: [ADR-001](../architecture/ADR-001-agent-sdk.md) — the always-replace-pi-base / don't-inherit-operator-append stance
- See also: [DES-014](DES-014-two-tier-agent-facing-documentation.md) — the two-tier convention for everything that supplements a base prompt (usage sections, reference pages), including what the core prompt may say
- Related feature: [../feature-designs/foundational-context.md](../feature-designs/foundational-context.md) — the main-session identity and the two-tier placement matrix (DES-014)
- Related feature: [../feature-designs/agent-integration.md](../feature-designs/agent-integration.md) — `isolatePrompt` and the side-run seam
