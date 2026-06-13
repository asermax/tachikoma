# Skills

<!-- This spec describes the current system capability. Updated through delta reconciliation. -->

## Overview

Skills give the agent packaged expertise: Agent Skills-format directories from both the user's workspace (`{workspace}/skills/`) and the repo-root `skills/` directory (built-in authoring skills) are contributed to pi as native skill sources, so pi's progressive disclosure surfaces each skill's description in the system prompt and the agent reads the full `SKILL.md` only when relevant. The extension itself stays thin — it wires the sources, adds a `/reload` path so the live session can pick up skill changes, and adds the one capability pi does not cover: agent definitions bundled inside skills become delegable subagents through a `delegate_to_agent` tool.

## User Stories

- As a user, I want to drop skill packages into my workspace skills directory so that the agent gains specialized knowledge without code changes or registry configuration
- As the assistant, I want skill descriptions available via progressive disclosure so that I load full skill content only when it is relevant to the task
- As a skill author, I want to bundle agent definitions inside a skill so that focused work can be delegated to an isolated subagent with its own prompt and tools

## Requirements

| ID | Requirement |
|----|-------------|
| R0 | A bootstrap hook creates `{workspace}/skills/` if missing (idempotent) |
| R1 | Two skill sources are contributed via the `resources_discover` event in every agent session: the workspace skills directory (`{workspace}/skills/`) and the built-in authoring skills shipped in the repo-root `skills/` directory; discovery, progressive disclosure, and `/skill:` commands are pi-native |
| R2 | Skills follow the Agent Skills standard (`SKILL.md` with YAML frontmatter) — no Tachikoma-specific skill format exists |
| R3 | Markdown files under `<skill>/agents/` are discovered as agent definitions, namespaced as `<skill>/<agent>` to prevent cross-skill collisions |
| R4 | Agent frontmatter: `description` is required (missing means the file is skipped with a warning); `name` defaults to the file stem; `tools` accepts a YAML list or a comma-separated string; `model` is an optional `provider/model-id[:thinkingLevel]` reference (a non-string value is warned and ignored, leaving the agent on the default tier); the markdown body is the agent's system prompt |
| R5 | A `delegate_to_agent` tool is registered when at least one agent exists at session creation; it runs the named agent headlessly with its own system prompt and tool set and returns the agent's final answer as the tool result (tail-truncated, with a truncation marker) |
| R6 | Agents that declare no tools run with the default read-only set: `read`, `grep`, `find`, `ls` |
| R6a | An agent that declares a `model` runs its headless delegation on that model; an agent without one runs on the side-runner's default tier |
| R7 | Invalid agent files are logged and skipped; a missing skills root or agents directory yields empty discovery, never an error |
| R8 | Agent discovery re-runs at every session creation and on every `delegate_to_agent` execution, so new and edited agents apply without a restart |
| R9 | The extension can be disabled via `[extensions.skills] enabled = false` (default enabled) |
| R10 | A `/reload` command and a `reload_resources` tool reload skills, prompts, and extensions from disk into the running session; the tool queues `/reload` as a follow-up (reload must run in command context) so additions and edits apply mid-session without a restart |

## Behaviors

### Skill Source Contribution and Reload (R0, R1, R2, R10)

The session factory answers pi's `resources_discover` event with the workspace skills path and the built-in skills path; everything downstream (frontmatter parsing, description injection into the system prompt, on-demand `SKILL.md` reads) is pi's native pipeline. A `/reload` command and a `reload_resources` tool let a running session pick up skill changes without a restart.

**Acceptance Criteria**:
- Given the workspace has no `skills/` directory, when bootstrap runs, then the directory is created; a second run makes no changes
- Given a valid Agent Skills package in the workspace or built-in `skills/` directory, when an agent session is created, then pi surfaces the skill through progressive disclosure (description in the system prompt, content loaded on demand)
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

### Delegation (R5, R6, R6a)

The `delegate_to_agent` tool (`src/extensions/skills/delegate.ts`) lists discovered agents in its description and runs the chosen agent in an isolated headless session via `app.agent.side.run`, passing the agent's declared `model` when set.

**Acceptance Criteria**:
- Given discovered agents, when the tool description is built, then each agent appears as `<skill>/<name>: <description>`
- Given a delegation call with a known agent, when the tool executes, then the headless run uses the agent's system prompt, its declared tools, and the task as the prompt; the agent's final text is the tool result
- Given an agent with no declared tools, when delegated to, then the run uses the default read-only tool set
- Given an agent that declares a `model`, when delegated to, then the headless run is pinned to that model; an agent without a `model` runs on the side-runner's default tier
- Given an unknown agent name, when the tool executes, then it throws an error listing the available agents (no run is attempted)
- Given no agents exist at session creation, then the tool is not registered for that session

## Notes

Machinery deliberately left out of this implementation:

- **LLM skill classifier** — pi's progressive disclosure covers it: skill descriptions sit in the system prompt and the agent reads `SKILL.md` on demand, so no per-message classification pass (and its latency/cost) is needed
- **Skills hot-reload/watcher** — discovery runs per agent session, so new skills appear on the next session (topic boundary or restart) and agent definitions are re-read on every delegation; for the live session, `/reload` (and the `reload_resources` tool) re-reads resources on demand. No filesystem watcher — reload is explicit, not automatic
- **Skill `dependencies`** — not part of the Agent Skills standard. Skills that build on other material reference it directly in their content for the agent to read

See [../reference/pi-sdk-notes.md](../reference/pi-sdk-notes.md) (Skills section) for the pi-native behavior this extension relies on.
