# Skills

<!-- This spec describes the current system capability. Updated through delta reconciliation. -->

## Overview

Skills give the agent packaged expertise: Agent Skills-format directories from both the user's workspace (`{workspace}/skills/`) and the skills extension's bundled `builtin-skills/` directory (built-in authoring skills) are contributed to pi as native skill sources, so pi's progressive disclosure surfaces each skill's description in the system prompt and the agent reads the full `SKILL.md` only when relevant. On top of progressive disclosure, the extension proactively surfaces relevant skills: on each turn a conversation-aware classifier picks which loaded skills fit the latest message and injects a hidden recommendation telling the agent to load them, so the right expertise is pulled in instead of relying on the agent to notice it from the catalog alone. The extension otherwise stays thin — it wires the sources, adds a `/reload` path so the live session can pick up skill changes, and adds the one capability pi does not cover: agent definitions bundled inside skills become delegable subagents through a `delegate_to_agent` tool. A built-in `general-purpose` agent ships alongside the skill-bundled ones, so the main agent can always delegate focused, context-heavy work (e.g. exploring files) even with no skills installed.

## User Stories

- As a user, I want to drop skill packages into my workspace skills directory so that the agent gains specialized knowledge without code changes or registry configuration
- As the assistant, I want skill descriptions available via progressive disclosure so that I load full skill content only when it is relevant to the task
- As the assistant, I want relevant skills proactively recommended based on the conversation so that I load and apply their instructions even when I would not have chosen to on my own
- As a skill author, I want to bundle agent definitions inside a skill so that focused work can be delegated to an isolated subagent with its own prompt and tools
- As the assistant, I want an always-available general-purpose subagent so that I can offload context-heavy exploration and searching without a skill having to ship one

## Requirements

| ID | Requirement |
|----|-------------|
| R0 | A bootstrap hook creates `{workspace}/skills/` if missing (idempotent) |
| R1 | Two skill sources are contributed via the `resources_discover` event in every agent session: the workspace skills directory (`{workspace}/skills/`) and the built-in authoring skills bundled in the skills extension's `builtin-skills/` directory; discovery, progressive disclosure, and `/skill:` commands are pi-native |
| R2 | Skills follow the Agent Skills standard (`SKILL.md` with YAML frontmatter) — no Tachikoma-specific skill format exists |
| R3 | Markdown files under `<skill>/agents/` are discovered as agent definitions, namespaced as `<skill>/<agent>` to prevent cross-skill collisions |
| R4 | Agent frontmatter: `description` is required (missing means the file is skipped with a warning); `name` defaults to the file stem; `tools` accepts a YAML list or a comma-separated string; `model` is an optional `provider/model-id[:thinkingLevel]` reference (a non-string value is warned and ignored, leaving the agent on the default tier); the markdown body is the agent's system prompt |
| R5 | A `delegate_to_agent` tool is registered in every agent session while the skills extension is enabled (a built-in agent always exists); it runs the named agent headlessly with its own system prompt and tool set — with the prompt fully isolated via `isolatePrompt` (pi's append, project context files, and skills catalog suppressed) — and returns the agent's final answer as the tool result (tail-truncated, with a truncation marker) |
| R5a | A built-in `general-purpose` agent (bare name, no `<skill>/` namespace) is always available for delegation and is composed ahead of the discovered skill agents; it is a read-only worker (default tool set) for exploring/searching files and gathering information, with a self-contained worker system prompt owned in core ([DES-005](../design/DES-005-base-prompt-ownership.md)) |
| R5b | `delegate_to_agent` requires a `description` parameter — a short label for the delegation surfaced in tool-activity displays; it is display-only (required by the schema, but rendering degrades gracefully to the agent-only label when empty) and is never forwarded to the delegated run, which sees only the `task` |
| R6 | Agents that declare no tools run with the default read-only set: `read`, `grep`, `find`, `ls` |
| R6a | An agent that declares a `model` runs its headless delegation on that model; an agent without one runs on the side-runner's default tier |
| R7 | Invalid agent files are logged and skipped; a missing skills root or agents directory yields empty discovery, never an error |
| R8 | Agent discovery re-runs at every session creation and on every `delegate_to_agent` execution, so new and edited agents apply without a restart |
| R9 | The extension can be disabled via `[extensions.skills] enabled = false` (default enabled) |
| R10 | A `/reload` command and a `reload_resources` tool reload skills, prompts, and extensions from disk into the running session; the tool queues `/reload` as a follow-up (reload must run in command context) so additions and edits apply mid-session without a restart |
| R11 | The session factory (skill sources + `delegate_to_agent` + reload) is registered with `{ background: true }`, opting it into autonomous background task runs, so those runs also receive the skill sources and the `delegate_to_agent` tool (not just conversational sessions) |
| R12 | On each genuine top-level turn (main conversation and background task runs, not post-processing forks — gated by `app.agent.isForking()`), a conversation-aware classifier selects which loaded skills are relevant to the latest message and the extension injects a single hidden message recommending the agent load each newly matched skill via its `/skill:<name>` command; the eligible set excludes `disable-model-invocation` skills and skills already recommended this session (each skill is recommended at most once per session) |
| R13 | Proactive skill selection feeds the classifier the recent conversation, the latest user message, and the catalog of eligible skills (name + description); it returns skill names validated against the eligible set, and is best-effort — a classify failure or a timeout skips the recommendation and never blocks the response; a transient status line surfaces while it runs |
| R14 | Proactive recommendation is gated by `[extensions.skills] proactiveLoading` (default enabled); when disabled, no selection runs and skill loading falls back to pi's progressive disclosure alone |

## Behaviors

### Skill Source Contribution and Reload (R0, R1, R2, R10)

The session factory answers pi's `resources_discover` event with the workspace skills path and the built-in skills path; everything downstream (frontmatter parsing, description injection into the system prompt, on-demand `SKILL.md` reads) is pi's native pipeline. A `/reload` command and a `reload_resources` tool let a running session pick up skill changes without a restart.

**Acceptance Criteria**:
- Given the workspace has no `skills/` directory, when bootstrap runs, then the directory is created; a second run makes no changes
- Given a valid Agent Skills package in the workspace or the bundled `builtin-skills/` directory, when an agent session is created, then pi surfaces the skill through progressive disclosure (description in the system prompt, content loaded on demand)
- Given a skill is added while the app is running, when the next agent session is created, then the new skill is discovered; the running session can also pick it up via `/reload` or `reload_resources`
- Given the agent calls `reload_resources`, when it executes, then `/reload` is queued as a follow-up message (so it runs in command context) and resources refresh once the current run finishes

### Agent Discovery (R3, R4, R7, R8)

`discoverSkillAgents` (`src/extensions/skills/agents.ts`) scans every `<skill>/agents/*.md` file under the skills root and parses frontmatter metadata.

**Acceptance Criteria**:
- Given `skills/research/agents/scout.md` with a `description` and a YAML `tools` list, when discovery runs, then an agent named `research/scout` is returned with those tools and the body as its system prompt
- Given frontmatter declaring `name: pathfinder`, when discovery runs, then the agent is named `research/pathfinder` (explicit name wins over the file stem)
- Given `tools: read, ls` as a comma-separated string, when discovery runs, then the tools parse to `["read", "ls"]`
- Given `model: anthropic/claude-opus-4-5:high`, when discovery runs, then the agent's `model` is that reference; an agent without `model` discovers as `null`
- Given a non-string `model` value, when discovery runs, then a warning is logged and the agent loads with `model` `null` (the bad field never drops the agent)
- Given an agent file without a `description`, when discovery runs, then the file is skipped with a warning and other agents still load
- Given a skill without an `agents/` directory, or a missing skills root, when discovery runs, then an empty list is returned

### Delegation (R5, R5a, R5b, R6, R6a)

The `delegate_to_agent` tool (`src/extensions/skills/delegate.ts`) lists the available agents (the built-in `general-purpose` agent first, then discovered skill agents) in its description and runs the chosen agent in a fully isolated headless session via `app.agent.side.run` (`isolatePrompt: true`), passing the agent's declared `model` when set.

**Acceptance Criteria**:
- Given the built-in agent plus discovered skill agents, when the tool description is built, then `general-purpose` is listed first, followed by each skill agent as `<skill>/<name>: <description>`
- Given a delegation call with a known agent, when the tool executes, then the headless run uses the agent's system prompt, its declared tools, the task as the prompt, and `isolatePrompt: true` (the run sees only the agent's own prompt); the agent's final text is the tool result
- Given an agent with no declared tools, when delegated to, then the run uses the default read-only tool set
- Given an agent that declares a `model`, when delegated to, then the headless run is pinned to that model; an agent without a `model` runs on the side-runner's default tier
- Given an unknown agent name, when the tool executes, then it throws an error listing the available agents (including `general-purpose`); no run is attempted
- Given no skill agents exist, when a session is created and the skills extension is enabled, then `delegate_to_agent` is still registered and lists the built-in `general-purpose` agent; when the extension is disabled, no `delegate_to_agent` tool is registered
- Given a delegation call that includes a `description`, when the tool executes, then tool-activity displays surface the `description` alongside the agent, while the delegated run receives only the `task` as the prompt (the run options contain no `description`)

### Proactive Skill Loading (R12, R13, R14)

On each genuine top-level turn, a `before_agent_start` handler runs a conversation-aware classifier over the eligible skills and injects a hidden recommendation to load any match, so relevant skills reach the agent even when it would not pick them from the catalog itself. The pass augments progressive disclosure (descriptions stay in the system prompt) rather than replacing it, runs silently apart from a transient status line, and degrades to no recommendation on any failure.

**Acceptance Criteria**:
- Given eligible skills and a relevant latest message, when the classifier selects a skill, then a single hidden message is injected recommending the agent load it via `/skill:<name>` (with its description, no filesystem path); multiple matches share one message
- Given a skill already recommended earlier in the session, when it is selected again, then it is not recommended again
- Given the turn runs inside a post-processing fork (memory/core-context extraction), when the handler fires, then it skips entirely — no classification, no recommendation
- Given the classifier returns a name not among the eligible skills, or a `disable-model-invocation` skill exists, then that name/skill is never recommended
- Given the classifier fails or times out, then the recommendation is skipped and the response is never blocked
- Given no skills are eligible (none loaded, all disabled, or all already recommended), then no classifier call and no status line occur
- Given `[extensions.skills] proactiveLoading` is disabled, then no proactive selection runs and loading falls back to progressive disclosure alone

## Notes

Machinery deliberately left out of this implementation:

- **Skills hot-reload/watcher** — discovery runs per agent session, so new skills appear on the next session (topic boundary or restart) and agent definitions are re-read on every delegation; for the live session, `/reload` (and the `reload_resources` tool) re-reads resources on demand. No filesystem watcher — reload is explicit, not automatic
- **Skill `dependencies`** — not part of the Agent Skills standard. Skills that build on other material reference it directly in their content for the agent to read

See [../reference/pi-sdk-notes.md](../reference/pi-sdk-notes.md) (Skills section) for the pi-native behavior this extension relies on.
