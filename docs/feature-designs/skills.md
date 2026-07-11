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

Seven small modules. `index.ts` wires: a bootstrap hook ensures the workspace skills directory, and a session factory answers `resources_discover` with both that path and the extension's bundled `builtin-skills/` directory (built-in authoring skills) — pi does the rest. `reload.ts` registers the `/reload` command and the `reload_resources` tool for mid-session resource refresh. `agents.ts` scans `<skill>/agents/*.md` into `SkillAgent` records. `builtins.ts` ships the `general-purpose` agent (a `SkillAgent` whose prompt is the core-owned `SUBAGENT_SYSTEM_PROMPT`, see [DES-005](../design/DES-005-base-prompt-ownership.md)). `delegate.ts` builds the `delegate_to_agent` tool from a `discover` callback (which prepends the built-ins to the discovered skill agents) and an `AgentRunner`, so both inputs are injectable in tests; because a built-in always exists, the tool is registered unconditionally and every delegated run is isolated (`isolatePrompt: true`). `suggest.ts` registers the `before_agent_start` handler for proactive loading: a conversation-aware classifier picks relevant skills and injects their full content directly as a hidden message behind an authority-framed preface (the matched skills define the correct process — follow them rather than improvise), each wrapped with a per-skill adherence line, so no separate `/skill` load is needed (gated by `proactiveLoading`). `usage.ts` ships the agent-facing `skills-usage` guidance section (catalog habit plus injected-skill authority), contributed via `provideContext` and scoped to `["main", "background"]` so the core base prompt stays feature-agnostic ([DES-005](../design/DES-005-base-prompt-ownership.md)) — skills is the only capability extension that previously baked its guidance into the core prompt; it now owns it like git/workflows/tasks do.

## Components

### Implementation Structure

| Component | Responsibility | Key Decisions |
|-----------|----------------|---------------|
| `src/extensions/skills/index.ts` | Extension wiring: `ensure-skills-dir` bootstrap hook, `resources_discover` contributing the workspace and repo-root `skills/` paths, reload registration, unconditional `delegate_to_agent` registration, and `proactiveLoading`-gated suggestion registration | Built-in skills resolved relative to the module (`../../../skills` → repo root) so they ship with the install; `discover()` prepends `BUILTIN_AGENTS` to the scanned skill agents, so a built-in always exists and the tool is registered in every session (within the enabled extension) — no dead-tool guard needed; the factory is registered with `{ sessionScopes: ["main", "background"] }` so background task runs also get the skill sources, delegation, and proactive loading; it receives its binding `{ scope }` and passes a no-op `status` to the proactive classifier for background sessions (which have no streaming renderer), so the "Checking for relevant skills…" line never surfaces there — the classifier still runs, only its status is suppressed |
| `src/extensions/skills/suggest.ts` | `registerSkillSuggestion`: the `before_agent_start` proactive-loading handler — invocable-skill filter (drop `disableModelInvocation`), a per-branch `injected` Set that routes already-injected selected skills to a lightweight reminder, conversation-aware `classify` over all invocable skills, full-content injection for not-yet-injected skills | Depends on `Pick<SideRunner, "classify">` + injected `isForking`/`status`/`log`/`readSkill`/`onTopicChanged` for test fakes; skips when `isForking()` (excludes memory/core-context post-processing forks — see [agent-integration](agent-integration.md)); the `injected` Set is per-branch, not per-session — the daily trunk is one pi session for the whole day, so the factory wires `onTopicChanged` (main scope only) to the boundary's `session:topic-changed` event and subscribes to pi's `session_compact` event — either can remove injected content from the active path (a collapsed branch, or mid-branch compaction summarizing older entries away), so both clear the set so the skill re-evaluates from scratch and is re-injected in full if still relevant (issue-425: without the compaction clear, a skill stayed "injected" after its content was summarized out of context); prior context from `buildSessionContext(getEntries(), getLeafId())` (the just-submitted message is not yet persisted, so it appends `event.prompt` as the latest turn); `classifyWithDeadline` wraps classify in an `AbortController` whose signal is forwarded into the provider call, so a slow classify is cancelled at the deadline rather than left racing; reads each not-yet-injected match's body (frontmatter stripped via pi's `stripFrontmatter`, matching what `/skill` injects) via an injectable `readSkill` reader (default `readFileSync`; per-skill try/catch so one unreadable or empty file skips only that skill — logged — without aborting the rest, and skipped skills stay retryable) and routes the selection: the classifier sees every invocable skill each turn (not just not-yet-injected ones); a selected skill already in the set is re-anchored as a lightweight `<skill-reminder name="<name>">` (no body), while a selected skill not in the set is injected in full as `<injected-skill name="<name>">…</injected-skill>` — each wrapper ending with a per-skill adherence line ("→ Follow the instructions above for this task. If a workflow is defined, start it.") — and recorded in the set. One hidden `display:false` `skill-content` message holds whichever of full and reminder sections applied, each behind its own preface (full: the matched skills define the correct process for the request — follow them rather than improvise; their content is already here, so no `/skill` load is needed; reminder: a skill loaded earlier is relevant again — re-apply its already-in-context instructions); best-effort try/catch → no injection on any classify failure |
| `src/extensions/skills/usage.ts` | `SKILLS_USAGE`: the agent-facing skill-guidance section | Contributed via `provideContext(SKILLS_USAGE, "skills-usage")` (scoped `["main", "background"]`, gated by `enabled`), so the core base prompt stays feature-agnostic ([DES-005](DES-005-base-prompt-ownership.md)) — skills owns its guidance like git/workflows/tasks do, rather than baking it into `prompts.ts`; carries the catalog habit (read a fitting skill's `SKILL.md`) plus the injected-skill authority (follow a proactively loaded skill's workflows rather than improvising; treat it as authoritative for its domain) |
| `src/extensions/skills/builtins.ts` | `BUILTIN_AGENTS`: agents shipped with Tachikoma rather than bundled in a skill | The `general-purpose` agent uses a bare name (no `<skill>/` namespace, so it cannot collide with discovered agents), `tools: null` (delegate's default read-only set), `model: null` (default tier), and the core-owned `SUBAGENT_SYSTEM_PROMPT` ([DES-005](../design/DES-005-base-prompt-ownership.md)) |
| `src/extensions/skills/reload.ts` | `registerReload`: the `/reload` command (calls `ctx.reload()`) and the `reload_resources` tool that queues `/reload` as a follow-up | Reload must run in command context, so the tool re-injects `/reload` via `pi.sendUserMessage(..., { deliverAs: "followUp" })` rather than reloading inline |
| `src/extensions/skills/agents.ts` | `discoverSkillAgents`: scan skills root for `agents/*.md`, parse frontmatter via pi's `parseFrontmatter` | Synchronous fs reads (small trees, called at session creation and tool execution); per-file error isolation — one bad definition never blocks the rest; names namespaced `<skill>/<agent>`; optional `model` parsed as a non-empty string (validated against the registry only at delegation time) — a non-string value warns and falls back to `null` rather than dropping the agent; frontmatter `tools` names are validated case-sensitively against the built-ins (`read`,`grep`,`find`,`ls`,`bash`,`edit`,`write`, in `tool-names.ts`) — an unknown name (e.g. `Glob`/`WebSearch`) is warned and dropped while valid ones are kept, and all-invalid falls back to `null` (read-only default) rather than dropping the agent; an `extensionTools` field (same YAML-list / comma-separated-string format) declares exposed extension tools the agent needs, parsed like `tools` but *not* name-validated (resolved source-agnostically at execute time, [ADR-015](../architecture/ADR-015-subagent-extension-tool-grants.md)); a malformed `tools`/`extensionTools` format warns and falls back to `null` |
| `src/extensions/skills/delegate.ts` | `createDelegateTool`: the `delegate_to_agent` `ToolDefinition` | Depends on `AgentRunner = Pick<SideRunner, "run">` for test fakes; output truncated with pi's `truncateTail`; agent frontmatter `tools` accepts YAML list or comma-separated string (matches pi's subagent example); a declared `model` is threaded into `side.run` to pin the delegated run's model; every run passes `isolatePrompt: true` so no delegated agent inherits pi's append / project context files / skills catalog; a required display-only `description` param labels each delegation for tool-activity displays and is never forwarded to the run; an optional per-delegation `tools` param fully overrides the agent's built-in tool set, validated by the pure `resolveTools` against pi's built-ins (`read`, `grep`, `find`, `ls`, `bash`, `edit`, `write`) — the param is built-in-only, so an unknown name throws a self-correcting error listing the built-ins and directing extension/web tools to `extensionTools`, and an empty/omitted value falls back to the agent's declared tools then the read-only default; an additive `extensionTools` param grants exposed extension tools on top of the resolved built-ins, merged (union, de-duplicated) with the agent's frontmatter-declared `extensionTools` via the pure `resolveExtensionTools` (so a declared grant is auto-applied and a caller can extend but never narrow it) — it is NOT validated against built-ins here but resolved source-agnostically against the opened subagent session in `SideRunner.run` (see [ADR-015](../architecture/ADR-015-subagent-extension-tool-grants.md)), so an empty/omitted value means none granted (the built-in-allowlist path); the resolved built-ins and granted extension tools compose into one active set via `setActiveToolsByName`; the tool's description / `promptSnippet` / `promptGuidelines` advertise the grant generically (grantable names are only knowable after a session opens). Agents may set `dynamicPrompt(tools)` so the built-in `general-purpose` worker is rebuilt via `buildSubagentSystemPrompt` to match the granted tools (skill agents keep their own prompt) |

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
- Con: automatic mid-session pickup of *new* skills still does not happen — discovery is at session creation, and the daily trunk is one pi session for the whole day (topic shifts are in-session branch collapses, not new sessions), so a live session refreshes only on explicit `/reload` / `reload_resources`

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

### Per-delegation tool selection, validated against the built-ins

**Choice**: An optional `tools` parameter on `delegate_to_agent` fully overrides the agent's tool set for that run; a non-empty value wins over the agent's declared tools, which in turn win over the read-only default (`resolveTools`: `params.tools` → `agent.tools` → `DEFAULT_AGENT_TOOLS`). The value is validated against pi's built-in tools (`read`, `grep`, `find`, `ls`, `bash`, `edit`, `write`) and an unknown name throws a self-correcting error. The built-in `general-purpose` agent carries a `dynamicPrompt(tools)` so its worker prompt is rebuilt from the granted tools (`buildSubagentSystemPrompt`) — a worker handed `bash` is told it may run commands instead of being told it is read-only; skill agents keep their author-authored prompt. An empty array is treated as "not specified", never "no tools".
**Why**: Lets the calling agent grant a subagent exactly the tools a task needs (shell commands via `bash`, file changes via `edit`/`write`) without a new abstraction, composing with the existing agent-declared `tools`. Replace-semantics (a complete list) is predictable and matches how an agent's declared tools already work. Throwing on unknown tools mirrors the unknown-agent error and steers the model to request extension/web tools via the separate `extensionTools` param rather than mixing them into `tools`.
**Consequences**:
- Pro: shell-capable and file-modifying subagents are now possible per delegation, while read-only stays the default and the run stays fully isolated (`bare`/`inMemory`/`isolatePrompt` unchanged)
- The `tools` param stays built-in-only (a complete built-in override); extension and web tools are granted additively via the separate `extensionTools` param, resolved source-agnostically at execute time — see [ADR-015](../architecture/ADR-015-subagent-extension-tool-grants.md) and the "Extension-tool grants" decision below

### Extension-tool grants via a source-agnostic binding

**Choice**: Exposed extension tools (a factory tool or a pi-native extension tool) are granted to a delegated run via an additive `extensionTools` param, resolved source-agnostically against the *opened subagent session's* `AgentSession.getAllTools()` at execute time — one membership check covers both providers identically. A Tachikoma factory opts its tools into grantability through the `subagent` session scope (`app.agent.use(f, { sessionScopes: […, "subagent"] })`, per-factory); a pi-native extension is grantable by virtue of being installed (it cannot call `app.agent.use`). After validation the active set is narrowed to exactly the resolved built-ins plus the granted tools via `setActiveToolsByName`. An agent may also declare its needed extension tools in frontmatter (`extensionTools`); those are auto-granted on every delegation, merged additively (union, de-duplicated — a caller can extend but not narrow) with any caller-provided `extensionTools` via `resolveExtensionTools`, reusing this same source-agnostic resolution path. The `delegate_to_agent` description advertises the capability generically (grantable names are only knowable after a session opens).

**Why**: Tool names are only known after a factory runs inside a session, so a post-open membership check is the precise way to validate a requested name regardless of provider — this unifies Tachikoma-factory and pi-native tools in one code path without a tool-name declaration contract. The grant reuses the background path's "drop the `tools` allowlist so bound factory tools register and are enumerable" insight, then narrows with `setActiveToolsByName`; isolation is preserved (the session is still `bare`, in-memory, prompt-isolated, and disposed — only the bound factory set changes to the `subagent` subset). See [ADR-015](../architecture/ADR-015-subagent-extension-tool-grants.md) for the full alternatives analysis.

**Alternatives Considered**: per-tool declaration, re-scoping individual tools, an inclusive `tools` allowlist, an `excludeTools` denylist, selective factory binding, and resolving against the main session's tool set were all considered and not chosen — see ADR-015 for the reasoning.

**Consequences**:
- Pro: tool-using work (e.g. web research) runs in the isolated subagent instead of the main session; one source-agnostic code path covers both provider types; the description never goes stale (it advertises a capability, not a list)
- Pro: the read-only built-in default is preserved and the built-in `tools` path (R6b) is unchanged
- Con: generic advertising means the model must know or name a tool to request it (discovering the name via the self-correcting error, which lists grantables, or from extension documentation) — mitigated by `promptGuidelines` naming the common case (web research → web tools)
- Con: name collisions are best-effort last-wins (pi's registry dedupes by name, so a collision is undetectable, especially for pi-native providers) and opt-in is all-or-nothing per factory — documented in ADR-015

## System Behavior

### Scenario: Delegation to a skill-bundled agent

**Given**: `skills/research/agents/scout.md` declares `description` and `tools: [read, grep]`
**When**: The main agent calls `delegate_to_agent(agent="research/scout", task="find sources on X")`
**Then**: A headless side session runs with the file body as system prompt and only `read`/`grep` available; the run is isolated (`isolatePrompt: true`), so it does not inherit pi's append, project context files, or skills catalog; its final assistant text returns as the tool result, tail-truncated with an `[output truncated]` marker if oversized.

### Scenario: Always-available general-purpose delegation

**Given**: A workspace with no skill agents installed
**When**: The main agent calls `delegate_to_agent(agent="general-purpose", task="find where X is configured")`
**Then**: `delegate_to_agent` is registered regardless (the built-in is always discovered and listed first), and a fully isolated headless run executes with the core-owned `SUBAGENT_SYSTEM_PROMPT` and the default read-only tool set; its final text returns as the tool result.

### Scenario: Delegation with a per-delegation tool override

**Given**: A task that needs to run a shell command, not just read files
**When**: The main agent calls `delegate_to_agent(agent="general-purpose", task="report disk usage by extension", tools=["read", "grep", "bash"])`
**Then**: `resolveTools` returns `["read", "grep", "bash"]` (overriding the read-only default), the headless run is opened with exactly those tools and stays fully isolated (`isolatePrompt: true`), and the built-in worker's prompt is rebuilt via `buildSubagentSystemPrompt` so it is told it may run commands rather than being read-only.

### Scenario: Delegation granting an exposed extension tool

**Given**: A web extension factory is registered with `app.agent.use(webFactory, { sessionScopes: ["subagent"] })` and registers `web_search`
**When**: The main agent calls `delegate_to_agent(agent="general-purpose", task="research X", extensionTools=["web_search"])`
**Then**: The subagent session opens in-memory, prompt-isolated (`isolatePrompt: true`), with the web factory bound (`{ scope: "subagent" }`); `web_search` resolves via `getAllTools()`; the active set narrows to the read-only built-ins plus `web_search`; the run executes and its final text returns as the tool result. The session is disposed after.

### Scenario: Delegation requesting an unknown extension tool

**Given**: The main agent requests `extensionTools=["web_srch"]` (a typo)
**When**: The delegation executes
**Then**: The subagent session opens with the `subagent` factories bound, `getAllTools()` is enumerated, `web_srch` is not found, the session is disposed, and the delegation throws a self-correcting error listing the grantable (non-builtin) tool names — *before* any prompt runs. No run is attempted.

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
**Then**: The handler filters to invocable skills, surfaces a transient "Checking for relevant skills…" status, hands the recent conversation + latest message + skill catalog to `classify`, and on a `pdf-tools` match (not yet injected this branch) reads its `SKILL.md` and injects one hidden `skill-content` message with the full content wrapped in `<injected-skill name="pdf-tools">…</injected-skill>` (ending with a per-skill adherence line) behind an authority-framed preface — so the agent has the instructions in hand and is told to follow them rather than improvise, without loading it. A later turn on the same branch that selects `pdf-tools` again injects a lightweight `<skill-reminder>` instead of re-injecting the full body (its content is already in context); an unrelated message injects nothing. If the session then compacts (`session_compact`), or after a genuine topic shift (an auto-detected shift, `/new`, or a forced jump to an earlier branch — the boundary emits `session:topic-changed`), the per-branch `injected` record clears and `pdf-tools` re-evaluates from scratch — re-injected in full if it is relevant again, since its earlier content left the active path (summarized away by compaction, or on the collapsed prior branch).

### Scenario: Proactive injection suppressed inside post-processing

**Given**: Memory extraction forks the just-ended conversation (`forkAndContinue`)
**When**: The forked session's `before_agent_start` fires (the skills factory is bound, since the fork is non-bare)
**Then**: `app.agent.isForking()` is true, so the handler returns immediately — no classify call, no injection — leaving proactive loading to genuine top-level turns only.

### Scenario: Proactive loading runs in a background task session without surfacing status

**Given**: An autonomous background task session is opened (the skills factory is bound, since it opts into the `"background"` scope)
**When**: The session's `before_agent_start` fires on a genuine top-level turn (`isForking()` is false)
**Then**: The proactive classifier still runs and injects matched skills (background tasks keep proactive skill injection), but the "Checking for relevant skills…" status is suppressed — the factory receives `{ scope: "background" }` and passes the classifier a no-op `status`, since a background session has no streaming renderer to host the line and no main `respond()` to reclaim a lead-in, so it would otherwise orphan as a stray.

## Notes

- The headless runner (`SideRunner.run`) opens a bare in-memory pi session by default: nothing persisted, no Tachikoma extensions, processor-tier model by default; an optional `model` reference on the run options pins the model instead, resolved through `ModelTiers.resolveRef` (same `provider/model-id[:thinkingLevel]` form as `[agent]` tier config). Delegated runs additionally set `isolatePrompt: true`, so the worker sees only its own prompt (see [agent-integration](agent-integration.md)). A delegation that requests `extensionTools` opens instead with the `subagent`-scoped factories bound and no `tools` allowlist (so every bound factory tool registers), validates each requested name source-agnostically via `session.getAllTools()`, then narrows the active set via `session.setActiveToolsByName([...resolvedBuiltins, ...granted])` — the granted tools run inside the same isolated, disposed run (see [ADR-015](../architecture/ADR-015-subagent-extension-tool-grants.md))
- The built-in `general-purpose` agent cannot itself delegate: the skills/delegate factory is scoped `["main", "background"]` and deliberately not `"subagent"`, so even when a subagent session binds `subagent`-scoped factories (which it does only when `extensionTools` is requested), `delegate_to_agent` is never registered there — recursion stays structurally impossible, no runtime guard needed (see [ADR-015](../architecture/ADR-015-subagent-extension-tool-grants.md))
- Proactive injection is best-effort and silent: the injected `skill-content` message is `display:false` (the adapter surfaces only assistant text/thinking/tool events, so nothing reaches the channels), and the only visible surface is the transient status line (suppressed for background task sessions, which have no channel renderer — the factory passes the classifier a no-op `status` there, so the line never surfaces and cannot orphan). A skill whose content cannot be read (or is empty) is skipped with a warn/debug log and stays retryable; if every match is skipped, nothing is injected. The `injected` Set (successful full injections only) is per-branch and lives in the factory-invocation closure: a new/resumed session re-evaluates from scratch, and within the daily trunk both a `session:topic-changed` event (auto-shift, `/new`, or earlier-branch jump) and pi's `session_compact` event clear it — a collapsed branch and mid-branch compaction each remove injected content from the active path, so either triggers re-evaluation. A selected skill already in the set is re-anchored as a lightweight `<skill-reminder>` rather than re-injected in full; once cleared, it is injected in full again if still relevant (a benign re-injection at worst). See [agent-integration](agent-integration.md) for `isForking()`.
- Tests: `tests/skills/agents.test.ts` (discovery against tmp-dir fixtures), `tests/skills/builtins.test.ts` (built-in agent shape), `tests/skills/delegate.test.ts` (tool behavior against a faked runner, including the built-in and isolation), `tests/skills/index.test.ts` (unconditional registration, the background-task opt-in, the `proactiveLoading` gate/default, and the session-scope status suppression — main surfaces the status, background runs the classifier but suppresses it), `tests/skills/suggest.test.ts` (proactive content injection against a faked classifier + faked `readSkill`: eligibility, isForking skip, timeout, multi-skill packing, dedup, per-skill read-failure/empty skip, retry, status, classify payload; plus `SkillSelectionSchema` defaulting a missing `skills` key to an empty selection), `tests/skills/reload.test.ts` (the `/reload` command and the `reload_resources` tool registered by `reload.ts`), `tests/agent/manager.test.ts` (`isForking()` across nested/parallel forks), `tests/agent/prompts.test.ts` (role prompt composition)
