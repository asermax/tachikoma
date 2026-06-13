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

Three small modules. `index.ts` wires: a bootstrap hook ensures the skills directory, and a session factory answers `resources_discover` with that path — pi does the rest. `agents.ts` scans `<skill>/agents/*.md` into `SkillAgent` records. `delegate.ts` builds the `delegate_to_agent` tool from a `discover` callback and an `AgentRunner`, so both inputs are injectable in tests.

## Components

### Implementation Structure

| Component | Responsibility | Key Decisions |
|-----------|----------------|---------------|
| `src/extensions/skills/index.ts` | Extension wiring: `ensure-skills-dir` bootstrap hook, `resources_discover` contribution, conditional `delegate_to_agent` registration | Tool registered only when `discover()` finds at least one agent at session creation, so an agent-less workspace advertises no dead tool |
| `src/extensions/skills/agents.ts` | `discoverSkillAgents`: scan skills root for `agents/*.md`, parse frontmatter via pi's `parseFrontmatter` | Synchronous fs reads (small trees, called at session creation and tool execution); per-file error isolation — one bad definition never blocks the rest; names namespaced `<skill>/<agent>` |
| `src/extensions/skills/delegate.ts` | `createDelegateTool`: the `delegate_to_agent` `ToolDefinition` | Depends on `AgentRunner = Pick<SideRunner, "run">` for test fakes; output truncated with pi's `truncateTail`; `tools` accepts YAML list or comma-separated string (matches pi's subagent example) |

## Key Decisions

### Delegate skill discovery to pi instead of owning a registry/classifier

**Choice**: Contribute the skills directory through `resources_discover` and let pi own discovery, relevance, and loading.
**Why**: pi implements the Agent Skills standard natively with progressive disclosure — descriptions in the system prompt, content read on demand. A separate classifier would duplicate pi at extra cost and latency per message.
**Alternatives Considered**:
- A `SkillRegistry` + LLM classifier: full control over injection, but a per-message LLM call and a parallel skill pipeline pi would ignore
- `skillsOverride` on the resource loader: replaces pi's whole skill set rather than adding a source; wrong altitude for one extra directory

**Consequences**:
- Pro: zero per-message cost; skill behavior matches pi documentation exactly
- Pro: the extension is ~50 lines of wiring
- Con: no mid-session pickup of new skills — discovery happens at session creation (acceptable: sessions are replaced at every topic boundary)
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
- Con: if zero agents existed at session creation, the tool is absent for that whole session

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
**Then**: A headless side session runs with the file body as system prompt and only `read`/`grep` available; its final assistant text returns as the tool result, tail-truncated with an `[output truncated]` marker if oversized.

### Scenario: Unknown agent name

**Given**: The agent mistypes the namespace
**When**: `delegate_to_agent(agent="scout", ...)` executes
**Then**: The tool throws `Unknown agent "scout"` including the current agent list, so the model can retry with a valid name. No side run starts.

### Scenario: Skill authored mid-session

**Given**: The agent scaffolds a new skill with an `agents/` directory during a conversation
**When**: It immediately delegates to the new agent
**Then**: Execution-time rediscovery finds it (if the tool was registered for this session); the skill's own `SKILL.md` becomes visible to pi at the next session creation.

## Notes

- The headless runner (`SideRunner.run`) opens a bare in-memory pi session: nothing persisted, no Tachikoma extensions, processor-tier model by default
- Tests: `tests/skills/agents.test.ts` (discovery against tmp-dir fixtures), `tests/skills/delegate.test.ts` (tool behavior against a faked runner)
