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

Six small modules. `index.ts` wires: a bootstrap hook ensures the workspace skills directory, and a session factory answers `resources_discover` with both that path and the extension's bundled `builtin-skills/` directory (built-in authoring skills) — pi does the rest. `reload.ts` registers the `/reload` command and the `reload_resources` tool for mid-session resource refresh. `agents.ts` scans `<skill>/agents/*.md` into `SkillAgent` records. `builtins.ts` ships the `general-purpose` agent (a `SkillAgent` whose prompt is the core-owned `SUBAGENT_SYSTEM_PROMPT`, see [DES-005](../design/DES-005-base-prompt-ownership.md)). `delegate.ts` builds the `delegate_to_agent` tool from a `discover` callback (which prepends the built-ins to the discovered skill agents) and an `AgentRunner`, so both inputs are injectable in tests; because a built-in always exists, the tool is registered unconditionally and every delegated run is isolated (`isolatePrompt: true`). `suggest.ts` registers the `before_agent_start` handler for proactive loading: a conversation-aware classifier picks relevant skills and injects their full content directly as a hidden message, so no separate `/skill` load is needed (gated by `proactiveLoading`).

## Components

### Implementation Structure

| Component | Responsibility | Key Decisions |
|-----------|----------------|---------------|
| `src/extensions/skills/index.ts` | Extension wiring: `ensure-skills-dir` bootstrap hook, `resources_discover` contributing the workspace and repo-root `skills/` paths, reload registration, unconditional `delegate_to_agent` registration, and `proactiveLoading`-gated suggestion registration | Built-in skills resolved relative to the module (`../../../skills` → repo root) so they ship with the install; `discover()` prepends `BUILTIN_AGENTS` to the scanned skill agents, so a built-in always exists and the tool is registered in every session (within the enabled extension) — no dead-tool guard needed; the factory is registered with `{ sessionScopes: ["main", "background"] }` so background task runs also get the skill sources, delegation, and proactive loading; it receives its binding `{ scope }` and passes a no-op `status` to the proactive classifier for background sessions (which have no streaming renderer), so the "Checking for relevant skills…" line never surfaces there — the classifier still runs, only its status is suppressed |
| `src/extensions/skills/suggest.ts` | `registerSkillSuggestion`: the `before_agent_start` proactive-loading handler — eligibility filter (drop `disableModelInvocation` + per-session `injected` Set), conversation-aware `classify`, full-content injection | Depends on `Pick<SideRunner, "classify">` + injected `isForking`/`status`/`log`/`readSkill` for test fakes; skips when `isForking()` (excludes memory/core-context post-processing forks — see [agent-integration](agent-integration.md)); prior context from `buildSessionContext(getEntries(), getLeafId())` (the just-submitted message is not yet persisted, so it appends `event.prompt` as the latest turn); `classifyWithDeadline` wraps classify in an `AbortController` whose signal is forwarded into the provider call, so a slow classify is cancelled at the deadline rather than left racing; reads each match's body (frontmatter stripped via pi's `stripFrontmatter`, matching what `/skill` injects) via an injectable `readSkill` reader (default `readFileSync`; per-skill try/catch so one unreadable or empty file skips only that skill — logged — without aborting the rest, and skipped skills stay retryable) and injects one hidden `display:false` `skill-content` message wrapping each match's body as `<injected-skill name="<name>">…</injected-skill>` behind a preface; best-effort try/catch → no injection on any classify failure |
| `src/extensions/skills/builtins.ts` | `BUILTIN_AGENTS`: agents shipped with Tachikoma rather than bundled in a skill | The `general-purpose` agent uses a bare name (no `<skill>/` namespace, so it cannot collide with discovered agents), `tools: null` (delegate's default read-only set), `model: null` (default tier), and the core-owned `SUBAGENT_SYSTEM_PROMPT` ([DES-005](../design/DES-005-base-prompt-ownership.md)) |
| `src/extensions/skills/reload.ts` | `registerReload`: the `/reload` command (calls `ctx.reload()`) and the `reload_resources` tool that queues `/reload` as a follow-up | Reload must run in command context, so the tool re-injects `/reload` via `pi.sendUserMessage(..., { deliverAs: "followUp" })` rather than reloading inline |
| `src/extensions/skills/agents.ts` | `discoverSkillAgents`: scan skills root for `agents/*.md`, parse frontmatter via pi's `parseFrontmatter` | Synchronous fs reads (small trees, called at session creation and tool execution); per-file error isolation — one bad definition never blocks the rest; names namespaced `<skill>/<agent>`; optional `model` parsed as a non-empty string (validated against the registry only at delegation time) — a non-string value warns and falls back to `null` rather than dropping the agent |
| `src/extensions/skills/delegate.ts` | `createDelegateTool`: the `delegate_to_agent` `ToolDefinition` | Depends on `AgentRunner = Pick<SideRunner, "run">` for test fakes; output truncated with pi's `truncateTail`; `tools` accepts YAML list or comma-separated string (matches pi's subagent example); a declared `model` is threaded into `side.run` to pin the delegated run's model; every run passes `isolatePrompt: true` so no delegated agent inherits pi's append / project context files / skills catalog; a required display-only `description` param labels each delegation for tool-activity displays and is never forwarded to the run |

## Key Decisions

### Delegate skill discovery to pi, and inject matched skills' full content via a conversation-aware classifier

**Choice**: Contribute the skills directory through pi's `resources_discover` so pi owns discovery, while a per-turn conversation-aware classifier (`suggest.ts`) picks the skills it judges relevant and injects their full `SKILL.md` content directly as a hidden, persisted message — so the model has the instructions in hand with no separate load step. Discovery stays pi-native; for matched skills, loading is short-circuited (their content is injected up front rather than left for the model to pull via `/skill`).
**Why**: pi implements the Agent Skills standard natively with progressive disclosure, so owning a parallel discovery/loading pipeline is redundant. But progressive disclosure leaves the *decision* to load to the model, which (per the SDK's own note) "does not always do this" — so a genuinely relevant skill can be skipped. Injecting matched skills' content directly removes both the reliance on the model honoring a nudge and the extra load round-trip, guaranteeing the model has the relevant instructions at the cost of injecting their tokens up front.
**Alternatives Considered**:
- **Recommending via a hidden `/skill:<name>` hint** (a leaner-context approach): considered and not chosen — it keeps the context leaner but costs an extra model turn to perform the load and still depends on the model honoring the nudge, so a relevant skill can still be skipped.
- **Full delegation to pi with no classifier** (zero per-message cost): considered and not chosen — it misses skills the model declines to load, and relevance is bounded by how well descriptions are written.
- **A true session-fork branch** that inherits pi's native skill catalog and picks via a tool: considered and not chosen — a throwaway session file and full agent turn per message, plus anti-recursion machinery.
- **An in-session `navigateTree` branch** (boomerang-style): considered and not chosen — it leaks the selection turn into the channel before collapsing it.
- **A `SkillRegistry` owning discovery + loading**, or `skillsOverride` on the resource loader: considered and not chosen — duplicates or replaces pi's whole skill pipeline; wrong altitude.

**Consequences**:
- Pro: relevant skills' instructions reach the model even when it would not pick them — with no extra load round-trip and no reliance on it honoring a recommendation; relevance uses conversation context, not just description quality
- Pro: the classifier is a thin, fakeable side-call reusing `SideRunner.classify`, and the content read is an injectable `readSkill` (default `readFileSync`) for test fakes
- Con: a per-turn `classifier`-tier round-trip on the hot path when eligible skills exist, plus the tokens of every matched skill's content injected up front (the trade versus recommending, which kept the context lean) — bounded by the no-eligible-skip, a short request-cancelling deadline, a recent-context window, the per-session `injected` Set (cost decays as a session's skills get injected), graceful per-skill skip on read failure, and disableable via `proactiveLoading`
- Con: automatic mid-session pickup of *new* skills still does not happen — discovery is at session creation (sessions are replaced at every topic boundary), and a live session refreshes only on explicit `/reload` / `reload_resources`

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

**Choice**: A single tool taking `agent`, `task`, and a display-only `description` parameter, listing available agents in its description.
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

### Scenario: Proactive loading of a relevant skill

**Given**: A `pdf-tools` skill is loaded and the user asks to merge some PDFs
**When**: The turn starts (`before_agent_start`), not inside a post-processing fork
**Then**: The handler filters eligible skills, surfaces a transient "Checking for relevant skills…" status, hands the recent conversation + latest message + skill catalog to `classify`, and on a `pdf-tools` match reads its `SKILL.md` and injects one hidden `skill-content` message with the full content wrapped in `<injected-skill name="pdf-tools">…</injected-skill>` behind a preface — so the agent has the instructions without loading it. A later turn in the same session does not inject `pdf-tools` again; an unrelated message injects nothing.

### Scenario: Proactive injection suppressed inside post-processing

**Given**: Memory extraction forks the just-ended conversation (`forkAndContinue`)
**When**: The forked session's `before_agent_start` fires (the skills factory is bound, since the fork is non-bare)
**Then**: `app.agent.isForking()` is true, so the handler returns immediately — no classify call, no injection — leaving proactive loading to genuine top-level turns only.

### Scenario: Proactive loading runs in a background task session without surfacing status

**Given**: An autonomous background task session is opened (the skills factory is bound, since it opts into the `"background"` scope)
**When**: The session's `before_agent_start` fires on a genuine top-level turn (`isForking()` is false)
**Then**: The proactive classifier still runs and injects matched skills (background tasks keep proactive skill injection), but the "Checking for relevant skills…" status is suppressed — the factory receives `{ scope: "background" }` and passes the classifier a no-op `status`, since a background session has no streaming renderer to host the line and no main `respond()` to reclaim a lead-in, so it would otherwise orphan as a stray.

## Notes

- The headless runner (`SideRunner.run`) opens a bare in-memory pi session: nothing persisted, no Tachikoma extensions, processor-tier model by default; an optional `model` reference on the run options pins the model instead, resolved through `ModelTiers.resolveRef` (same `provider/model-id[:thinkingLevel]` form as `[agent]` tier config). Delegated runs additionally set `isolatePrompt: true`, so the worker sees only its own prompt (see [agent-integration](agent-integration.md))
- The built-in `general-purpose` agent cannot itself delegate: subagent sessions are `bare`, so the skills extension factory never runs in them and `delegate_to_agent` is not registered there — recursion is structurally impossible, no runtime guard needed
- Proactive injection is best-effort and silent: the injected `skill-content` message is `display:false` (the adapter surfaces only assistant text/thinking/tool events, so nothing reaches the channels), and the only visible surface is the transient status line (suppressed for background task sessions, which have no channel renderer — the factory passes the classifier a no-op `status` there, so the line never surfaces and cannot orphan). A skill whose content cannot be read (or is empty) is skipped with a warn/debug log and stays retryable; if every match is skipped, nothing is injected. The per-session `injected` Set (successful injections only) lives in the factory-invocation closure, so a new/resumed session re-evaluates from scratch (a benign re-injection at worst). See [agent-integration](agent-integration.md) for `isForking()`.
- Tests: `tests/skills/agents.test.ts` (discovery against tmp-dir fixtures), `tests/skills/builtins.test.ts` (built-in agent shape), `tests/skills/delegate.test.ts` (tool behavior against a faked runner, including the built-in and isolation), `tests/skills/index.test.ts` (unconditional registration, the background-task opt-in, the `proactiveLoading` gate/default, and the session-scope status suppression — main surfaces the status, background runs the classifier but suppresses it), `tests/skills/suggest.test.ts` (proactive content injection against a faked classifier + faked `readSkill`: eligibility, isForking skip, timeout, multi-skill packing, dedup, per-skill read-failure/empty skip, retry, status, classify payload; plus `SkillSelectionSchema` defaulting a missing `skills` key to an empty selection), `tests/skills/reload.test.ts` (the `/reload` command and the `reload_resources` tool registered by `reload.ts`), `tests/agent/manager.test.ts` (`isForking()` across nested/parallel forks), `tests/agent/prompts.test.ts` (role prompt composition)
