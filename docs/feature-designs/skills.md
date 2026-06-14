# Design: Skills

<!-- This design describes the current implementation approach. Updated through delta reconciliation. -->

**Feature Spec**: [../feature-specs/skills.md](../feature-specs/skills.md)
**Status**: Current

## Purpose

Explain why the skills extension is a thin wiring layer over pi's native skill pipeline, and how skill-bundled agents — the one piece pi does not provide — are exposed as delegable subagents.

## Problem Context

Owning the entire skill lifecycle would mean a multi-source registry, a per-message LLM classifier, a filesystem watcher with debounce, and transitive dependency resolution. pi already implements the Agent Skills standard (the `SKILL.md` format Tachikoma's workspace skills use) with progressive disclosure built in, which makes all of that machinery redundant. What remains is deciding how skill-bundled agent definitions — not part of the standard — reach the agent.

**Constraints:**
- pi's API is never wrapped ([DES-001](../design/DES-001-unified-extension-api.md)); skill discovery must flow through pi's own `resources_discover` mechanism
- Skill-bundled agents have no pi-native equivalent — pi's subagent story is an example extension pattern, not a built-in
- Tests cannot hit a live model ([DES-002](../design/DES-002-extension-authoring.md)); delegation must run against a fakeable runner

**Interactions:**
- The session factory registered via `app.agent.use` is re-invoked on every agent session the coordinator creates (see [conversation-loop](conversation-loop.md))
- Delegation executes through `app.agent.side.run` (`src/agent/side-run.ts`): an ephemeral, in-memory pi session with no Tachikoma extensions bound
- Workflow definitions live inside skill packages (see [workflows](workflows.md)) but are loaded by the workflows extension independently

## Design Overview

Five small modules. `index.ts` wires: a bootstrap hook ensures the workspace skills directory, and a session factory answers `resources_discover` with both that path and the extension's bundled `builtin-skills/` directory (built-in authoring skills) — pi does the rest. `reload.ts` registers the `/reload` command and the `reload_resources` tool for mid-session resource refresh. `agents.ts` scans `<skill>/agents/*.md` into `SkillAgent` records. `builtins.ts` ships the `general-purpose` agent (a `SkillAgent` whose prompt is the core-owned `SUBAGENT_SYSTEM_PROMPT`, see [DES-005](../design/DES-005-base-prompt-ownership.md)). `delegate.ts` builds the `delegate_to_agent` tool from a `discover` callback (which prepends the built-ins to the discovered skill agents) and an `AgentRunner`, so both inputs are injectable in tests; because a built-in always exists, the tool is registered unconditionally and every delegated run is isolated (`isolatePrompt: true`).

## Components

### Implementation Structure

| Component | Responsibility | Key Decisions |
|-----------|----------------|---------------|
| `src/extensions/skills/index.ts` | Extension wiring: `ensure-skills-dir` bootstrap hook, `resources_discover` contributing the workspace and repo-root `skills/` paths, reload registration, unconditional `delegate_to_agent` registration | Built-in skills resolved relative to the module (`../../../skills` → repo root) so they ship with the install; `discover()` prepends `BUILTIN_AGENTS` to the scanned skill agents, so a built-in always exists and the tool is registered in every session (within the enabled extension) — no dead-tool guard needed; the factory is registered with `{ background: true }` so background task runs also get the skill sources and delegation |
| `src/extensions/skills/builtins.ts` | `BUILTIN_AGENTS`: agents shipped with Tachikoma rather than bundled in a skill | The `general-purpose` agent uses a bare name (no `<skill>/` namespace, so it cannot collide with discovered agents), `tools: null` (delegate's default read-only set), `model: null` (default tier), and the core-owned `SUBAGENT_SYSTEM_PROMPT` ([DES-005](../design/DES-005-base-prompt-ownership.md)) |
| `src/extensions/skills/reload.ts` | `registerReload`: the `/reload` command (calls `ctx.reload()`) and the `reload_resources` tool that queues `/reload` as a follow-up | Reload must run in command context, so the tool re-injects `/reload` via `pi.sendUserMessage(..., { deliverAs: "followUp" })` rather than reloading inline |
| `src/extensions/skills/agents.ts` | `discoverSkillAgents`: scan skills root for `agents/*.md`, parse frontmatter via pi's `parseFrontmatter` | Synchronous fs reads (small trees, called at session creation and tool execution); per-file error isolation — one bad definition never blocks the rest; names namespaced `<skill>/<agent>`; optional `model` parsed as a non-empty string (validated against the registry only at delegation time) — a non-string value warns and falls back to `null` rather than dropping the agent |
| `src/extensions/skills/delegate.ts` | `createDelegateTool`: the `delegate_to_agent` `ToolDefinition` | Depends on `AgentRunner = Pick<SideRunner, "run">` for test fakes; output truncated with pi's `truncateTail`; `tools` accepts YAML list or comma-separated string (matches pi's subagent example); a declared `model` is threaded into `side.run` to pin the delegated run's model; every run passes `isolatePrompt: true` so no delegated agent inherits pi's append / project context files / skills catalog |

## Key Decisions

### Delegate skill discovery to pi instead of owning a registry/classifier

**Choice**: Contribute the skills directory through `resources_discover` and let pi own discovery, relevance, and loading.
**Why**: pi implements the Agent Skills standard natively with progressive disclosure — descriptions in the system prompt, content read on demand. A separate classifier would duplicate pi at extra cost and latency per message.
**Alternatives Considered**:
- A `SkillRegistry` + LLM classifier: full control over injection, but a per-message LLM call and a parallel skill pipeline pi would ignore
- `skillsOverride` on the resource loader: replaces pi's whole skill set rather than adding a source; wrong altitude for one extra directory

**Consequences**:
- Pro: zero per-message cost; skill behavior matches pi documentation exactly
- Pro: the extension is thin wiring plus the small reload module
- Con: automatic mid-session pickup of new skills does not happen — discovery is at session creation (sessions are replaced at every topic boundary), and a live session refreshes only on explicit `/reload` / `reload_resources`
- Con: relevance quality is bounded by how well skill descriptions are written (progressive disclosure has no conversation-context classifier)

### Re-discover agents on every delegation instead of watching the filesystem

**Choice**: `delegate_to_agent` calls `discover()` again inside `execute`, and the session factory re-runs discovery per session; no watcher exists.
**Why**: Watcher + debounce + dirty-registry machinery only earns its keep when a long-lived in-memory registry must stay fresh. With cheap synchronous scans of a small directory tree, reading fresh at the moments that matter (session creation, tool execution) gets the same freshness with no lifecycle to manage.
**Alternatives Considered**:
- Filesystem watcher marking a registry dirty: more moving parts, shutdown handling, OS watch limits — for a directory scanned in microseconds

**Consequences**:
- Pro: agents created mid-session are delegable on the very next tool call
- Pro: no watcher lifecycle, no debounce tuning, nothing to leak
- Con: the tool's *description* (the agent list) is fixed at registration, so agents added mid-session are usable but not advertised until the next session
- The built-in `general-purpose` agent means `discover()` is never empty, so `delegate_to_agent` is always registered (within the enabled extension) — the old "no agents → no tool" gap is gone

### Explicit `/reload` for mid-session refresh instead of a watcher

**Choice**: `reload.ts` registers a `/reload` command that calls pi's `ctx.reload()` and a `reload_resources` tool. Because reload must run in command context, the tool does not reload inline — it queues `/reload` as a follow-up message via `pi.sendUserMessage("/reload", { deliverAs: "followUp" })`, so resources refresh once the current run finishes.
**Why**: New sessions always rediscover skills, but a long-running conversation would otherwise miss a skill the user just added or edited. An explicit command (and a tool the agent can invoke when the user mentions a skill change) covers the live session without a filesystem watcher, and keeps the reload on pi's sanctioned command path.
**Alternatives Considered**: A filesystem watcher auto-reloading on change (watcher lifecycle, debounce, OS limits for a rare event); having the tool reload inline (not possible — reload requires command context).
**Consequences**:
- Pro: the live session can pick up skill/prompt/extension changes without a restart
- Pro: no watcher to manage; reload is a deliberate, observable action
- Con: refresh is manual — the agent or user must trigger it; nothing reloads automatically

### One `delegate_to_agent` tool instead of a tool per agent

**Choice**: A single tool taking `agent` and `task` parameters, listing available agents in its description.
**Why**: Mirrors pi's subagent example pattern; keeps the tool surface stable while the agent set changes, and a wrong `agent` value fails with a self-correcting error that lists valid names.
**Consequences**:
- Pro: tool registration is independent of how many agents exist
- Con: agent selection is free-text — guarded by the error path rather than the schema

## System Behavior

### Scenario: Delegation to a skill-bundled agent

**Given**: `skills/research/agents/scout.md` declares `description` and `tools: [read, grep]`
**When**: The main agent calls `delegate_to_agent(agent="research/scout", task="find sources on X")`
**Then**: A headless side session runs with the file body as system prompt and only `read`/`grep` available; the run is isolated (`isolatePrompt: true`), so it does not inherit pi's append, project context files, or skills catalog; its final assistant text returns as the tool result, tail-truncated with an `[output truncated]` marker if oversized.

### Scenario: Always-available general-purpose delegation

**Given**: A workspace with no skill agents installed
**When**: The main agent calls `delegate_to_agent(agent="general-purpose", task="find where X is configured")`
**Then**: `delegate_to_agent` is registered regardless (the built-in is always discovered and listed first), and a fully isolated headless run executes with the core-owned `SUBAGENT_SYSTEM_PROMPT` and the default read-only tool set; its final text returns as the tool result.

### Scenario: Delegation to an agent with a declared model

**Given**: `skills/research/agents/analyst.md` declares `model: anthropic/claude-opus-4-5:high`
**When**: The main agent delegates to `research/analyst`
**Then**: The headless run is pinned to that model (the `model` reference flows `delegate.ts` → `side.run` → `manager.open` → `ModelTiers.resolveRef`); an agent without a `model` falls back to the side-runner's default tier.

### Scenario: Unknown agent name

**Given**: The agent mistypes the namespace
**When**: `delegate_to_agent(agent="scout", ...)` executes
**Then**: The tool throws `Unknown agent "scout"` including the current agent list, so the model can retry with a valid name. No side run starts.

### Scenario: Skill authored mid-session

**Given**: The agent scaffolds a new skill with an `agents/` directory during a conversation
**When**: It immediately delegates to the new agent
**Then**: Execution-time rediscovery finds it (the tool is always registered, since the built-in agent is always present); the skill's own `SKILL.md` becomes visible to pi after a `/reload` (or `reload_resources`) in the live session, and at the next session creation regardless.

## Notes

- The headless runner (`SideRunner.run`) opens a bare in-memory pi session: nothing persisted, no Tachikoma extensions, processor-tier model by default; an optional `model` reference on the run options pins the model instead, resolved through `ModelTiers.resolveRef` (same `provider/model-id[:thinkingLevel]` form as `[agent]` tier config). Delegated runs additionally set `isolatePrompt: true`, so the worker sees only its own prompt (see [agent-integration](agent-integration.md))
- The built-in `general-purpose` agent cannot itself delegate: subagent sessions are `bare`, so the skills extension factory never runs in them and `delegate_to_agent` is not registered there — recursion is structurally impossible, no runtime guard needed
- Tests: `tests/skills/agents.test.ts` (discovery against tmp-dir fixtures), `tests/skills/builtins.test.ts` (built-in agent shape), `tests/skills/delegate.test.ts` (tool behavior against a faked runner, including the built-in and isolation), `tests/skills/index.test.ts` (unconditional registration plus the background-task opt-in), `tests/skills/reload.test.ts` (the `/reload` command and the `reload_resources` tool registered by `reload.ts`), `tests/agent/prompts.test.ts` (role prompt composition)
