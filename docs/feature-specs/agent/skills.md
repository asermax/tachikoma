# Skill System and Sub-Agent Delegation

<!-- This spec describes the current system capability. Updated through delta reconciliation. -->

## Overview

The skill system provides a structured way to organize, detect, and delegate specialized sub-agents. Skills are directory-based packages containing YAML-formatted agent definitions. A skill registry discovers all skills at startup, with on-demand refresh when marked dirty by a filesystem watcher. On each message, a skills context provider classifies which skills are relevant to the user's message, considering only skills not already in context. Newly detected skills are appended as separate context entries with metadata identifying the skill name. Agents are derived from loaded skill entries and the skill registry on each message. Skills accumulate within a session — existing skills are never removed.

## User Stories

- As the system, I need a way to organize sub-agents into reusable skill packages so that specialized work can be delegated to focused agents
- As a skill developer, I want a clear directory structure and format so that I can define agents without coupling to the core system
- As the assistant, I want only relevant skills detected and loaded per session so that I have specialized knowledge and agents when needed without wasting context on irrelevant skills
- As the assistant, I want skills I create or modify during execution to become available without a restart so that skill authoring is a seamless experience

## Requirements

| ID | Requirement |
|----|-------------|
| R0 | Skill directory structure (SKILL.md + agents/ subdirectory) |
| R1 | Skill registry discovery at startup from multiple sources (built-in and workspace), with on-demand refresh when marked dirty |
| R2 | Agent definition loading from markdown files with YAML metadata |
| R3 | Agent namespacing to prevent collisions |
| R4 | Relevant agents loaded per-session based on detection results and passed to SDK for delegation |
| R5 | Agents available to the session accumulate as new skills are detected |
| R6 | Tool scoping via agent definition metadata |
| R7 | Bootstrap hook creates skills directory and shared registry with multiple sources, exposes via extras |
| R8 | Graceful error handling for invalid skills/agents |
| R9 | Skill detection via LLM: classify relevance using skill names, descriptions, and user message |
| R10 | Inject matched skill content (body without frontmatter) and directory path as `<skills>` XML context block |
| R11 | Skills accumulate per-message within a session — newly detected skills are appended as context entries; on topic shift (new session), all accumulated skills are cleared and classification runs fresh |
| R12 | Detection quality: balance precision (don't waste context on irrelevant skills) with recall (don't miss applicable skills) |
| R13 | When no skills exist in the registry, provider is a no-op (no context, no agents, no LLM call) |
| R14 | Graceful error handling for detection — failures never block the message; message proceeds with no skills/agents |
| R15 | Base system prompt preamble includes a static Skills section so the agent has foundational awareness of the skill system independent of per-session detection |
| R16 | Built-in skills ship with the package in src/tachikoma/skills/builtin/ |
| R17 | Multi-source registry: built-in scanned first, workspace second; workspace skills replace built-in on name collision |
| R18 | Registry created by skills_hook and exposed via ctx.extras["skill_registry"] |
| R19 | SkillsContextProvider receives registry via constructor injection (not owned internally) |
| R20 | Missing built-in directory logs warning and continues with workspace skills only |
| R21 | Filesystem watcher monitors the skills directory and marks the registry for refresh when changes occur |
| R22 | Burst changes during skill authoring coalesced into a single refresh via debounce |
| R23 | SkillsChanged event emitted via event bus when skill changes are detected |
| R24 | Watcher lifecycle managed through bootstrap (start) and graceful shutdown |
| R25 | Per-message skill re-evaluation — provider runs on every message, classifying skills not already in context |
| R26 | Registry filtering — classification only considers skills not already loaded (identified via entry metadata), preventing duplicates and reducing classifier input size |
| R27 | One context entry per detected skill, each with metadata identifying the skill name |
| R28 | Skip classification entirely when no unloaded skills remain in the registry (no LLM call, no latency) |

## Behaviors

### Skill Organization (R0)

Skills are directory-based packages in `workspace/skills/`. Each skill contains:
- `SKILL.md`: Metadata file with description and version (YAML frontmatter); the skill name is derived from the folder name
- `agents/`: Subdirectory containing agent definition files (optional if no agents)

Agent definitions are individual markdown files with YAML frontmatter containing description, model, tools, and a markdown body for system context.

**Acceptance Criteria**:
- Given skills are placed in the skills/ directory, when the registry loads, then all subdirectories are treated as potential skills
- Given a skill directory, when it contains a SKILL.md file with valid YAML frontmatter, then the skill is recognized as valid
- Given a skill, when agents/ subdirectory exists, then .md files within it are loaded as agent definitions
- Given a skill with no agents/ subdirectory, when loaded, then the skill is valid (agents are optional)

### Preamble Awareness (R15)

The base system prompt preamble includes a static Skills section that gives the agent foundational awareness of the skill system, independent of per-session detection.

**Acceptance Criteria**:
- Given the system prompt is assembled, then the preamble Skills section states that skills are specialized sub-agent packages in the `skills/` directory, without detailing internal structure (SKILL.md format, agents/ subdirectory, YAML fields — these are covered by the built-in authoring guide skill)
- Given the preamble Skills section, then it explains per-session detection and contextual injection of relevant skills
- Given the preamble Skills section, then it states the agent can create and manage skills by reading and writing files in the `skills/` directory
- Given the preamble Skills section, then it explicitly distinguishes from Claude Code's native skills and slash commands

### Skill Registry (R1, R2, R3, R17, R18, R19, R20)

The skill registry discovers all skills and agents at startup from multiple sources, building an indexed dictionary. The registry is created by the skills bootstrap hook and exposed via `ctx.extras["skill_registry"]`. The SkillsContextProvider receives it via constructor injection. When marked dirty by the filesystem watcher, it re-scans all sources on the next refresh, using swap-on-success to preserve the previous state on failure.

**Sources**: Built-in skills (shipped with the package in `src/tachikoma/skills/builtin/`) are scanned first, followed by workspace skills. Workspace skills completely replace built-in skills with the same name (last-wins precedence).

**Acceptance Criteria**:
- Given the registry initializes, when it scans both built-in and workspace directories, then all valid skills are discovered
- Given a skill with valid SKILL.md, when loaded, then all agents in its agents/ subdirectory are discovered
- Given agents from multiple skills, when indexed, then they are namespaced by skill (e.g., "skill-name/agent-name")
- Given an invalid skill, when the registry encounters it, then a warning is logged and loading continues
- Given the skills_hook runs, when it completes, then the registry is available in `ctx.extras["skill_registry"]`
- Given a workspace skill has the same name as a built-in skill, when loaded, then the workspace version completely replaces the built-in (metadata, body, and agents)
- Given the built-in directory doesn't exist, when the hook runs, then a warning is logged and the system continues with workspace skills only
- Given SkillsContextProvider is created, when it needs the registry, then it receives it via constructor injection (not owned internally)
- Given skills have changed on disk and the registry is marked dirty, when the provider triggers a refresh, then the registry re-discovers all sources reflecting additions, modifications, and deletions
- Given no changes have occurred since the last refresh, when the provider triggers a refresh, then the registry skips the re-scan
- Given the re-scan itself fails (e.g., permission error), then the registry retains its previous valid state, logs the error, and remains marked dirty for retry on the next refresh

### Coordinator Integration (R4, R5, R11)

The coordinator derives agents from context entries and the skill registry on each message, accumulating newly detected skills' agents alongside previously loaded ones.

**Acceptance Criteria**:
- Given the coordinator derives agents from context entries and the skill registry, when per-message evaluation detects new skills, then agents from newly detected skills are accumulated alongside previously loaded ones
- Given a new session starts after a topic shift, when the session starts, then agents are derived from the fresh classification results only
- Given a skill is deleted from the registry after being loaded into a session, when agents are derived from entries, then the deleted skill's agents are silently excluded from the session

### Tool Scoping (R6)

Agent definitions can specify which tools the agent is allowed to use.

**Acceptance Criteria**:
- Given an agent definition specifies a tools list, when the agent is created, then that constraint is included in the AgentDefinition
- Given an agent definition omits tools, when the agent is created, then the SDK applies default tool access

### Bootstrap (R7)

A bootstrap hook creates the skills directory if missing and creates the shared SkillRegistry with both built-in and workspace sources, stored in extras for use by the provider and watcher.

**Acceptance Criteria**:
- Given the bootstrap runs, when the skills hook executes, then the skills/ directory is created if it doesn't exist
- Given the skills directory already exists, when the hook runs again, then no action is taken (idempotent)
- Given the hook runs, when it completes, then `ctx.extras["skill_registry"]` contains a fully initialized SkillRegistry shared between provider and watcher
- Given the built-in directory exists, when the hook runs, then it's included in the registry's sources
- Given the built-in directory doesn't exist, when the hook runs, then a warning is logged and the registry only contains workspace skills

### Error Handling (R8)

Invalid skills and agents are gracefully skipped with diagnostic logging.

**Acceptance Criteria**:
- Given a skill is malformed, when the registry loads it, then a warning is logged and other skills load normally
- Given an agent definition is invalid, when loaded, then a warning is logged and the agent is skipped
- Given the registry encounters an error, then the coordinator continues with whatever agents were successfully loaded

### Filesystem Watching (R21, R22, R23, R24)

A filesystem watcher monitors `workspace/skills/` for changes and marks the registry for refresh. Changes are coalesced via debounce to prevent redundant refreshes during skill authoring. Mid-session stability is preserved by the accumulation model (R11) — refresh only affects classification of unloaded skills on subsequent messages.

**Acceptance Criteria**:
- Given the application starts, when the watcher task begins, then it monitors the skills directory for file additions, modifications, and deletions
- Given a burst of file changes occurs within a short window (e.g., skill authoring creating directory + SKILL.md + agent files), then a single registry mark and event are produced after the burst settles
- Given a skill change is detected, when the debounce window expires, then a SkillsChanged event is dispatched on the event bus
- Given the application shuts down, then the watcher task is cancelled gracefully without errors
- Given the skills directory does not exist at watcher start, then the watcher logs a warning and does not start
- Given the watcher encounters an unexpected error (e.g., OS watch limit exhausted), then it logs the error and stops gracefully, leaving the registry with its last known state

### Skill Detection (R9, R12, R13, R25, R26, R28)

On each message, the skills context provider classifies which skills are relevant to the user's message, considering only skills not already in context. Classification uses the same unified process for both initial and subsequent evaluations.

**Acceptance Criteria**:
- Given skills exist in the registry and an active session has skills A and B loaded, when a message relevant to skill C arrives, then skill C is classified, loaded, and appended to context
- Given the classification completes, when the response is parsed, then unrecognized skill names are discarded
- Given no skills exist in the registry, when the provider runs, then it returns immediately with no context and no agents (no LLM call made)
- Given all skills in the registry are already in context, when a new message arrives, then classification is skipped entirely (no LLM call)
- Given the classification code path, when comparing initial evaluation vs subsequent evaluation, then the same classification process is used (unified)

### Skill Content Injection (R10, R27)

Detected skills' content is injected as individual `<skills>` XML context blocks, one per detected skill, each with metadata identifying the skill name.

**Acceptance Criteria**:
- Given skills are detected as relevant, when the provider assembles results, then each detected skill is returned as a separate context entry with a `<skills>` XML block containing the skill's content body and directory path, and metadata with the skill name
- Given multiple skills are detected, when results are persisted, then separate context entries are appended for each
- Given no skills are detected as relevant, when the provider completes, then it returns no text context and no agent definitions

### Detection Error Handling (R14)

Detection failures are handled gracefully without blocking the message.

**Acceptance Criteria**:
- Given the skills detection agent fails (SDK error, timeout), when the provider catches the error, then it logs the failure and returns no context and no agents
- Given the detection agent returns an unrecognizable response (no valid skill names parseable), when the provider processes it, then it logs a warning and returns no context and no agents

## Out of Scope

- Custom MCP tools per-agent
- Skill-level markdown instructions
