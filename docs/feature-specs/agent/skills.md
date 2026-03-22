# Skill System and Sub-Agent Delegation

<!-- This spec describes the current system capability. Updated through delta reconciliation. -->

## Overview

The skill system provides a structured way to organize, detect, and delegate specialized sub-agents. Skills are directory-based packages containing YAML-formatted agent definitions. A skill registry discovers all skills at startup. On each new session, a skills context provider classifies which skills are relevant to the user's message, injects their content as context, and loads only the matched skills' agents into the SDK for delegation. Detection persists for the session — subsequent messages use the same detected skills without re-running classification.

## User Stories

- As the system, I need a way to organize sub-agents into reusable skill packages so that specialized work can be delegated to focused agents
- As a skill developer, I want a clear directory structure and format so that I can define agents without coupling to the core system
- As the assistant, I want only relevant skills detected and loaded per session so that I have specialized knowledge and agents when needed without wasting context on irrelevant skills

## Requirements

| ID | Requirement |
|----|-------------|
| R0 | Skill directory structure (SKILL.md + agents/ subdirectory) |
| R1 | Skill registry discovery at startup |
| R2 | Agent definition loading from markdown files with YAML metadata |
| R3 | Agent namespacing to prevent collisions |
| R4 | Relevant agents loaded per-session based on detection results and passed to SDK for delegation |
| R5 | Session-lifetime agent persistence |
| R6 | Tool scoping via agent definition metadata |
| R7 | Bootstrap hook for idempotent skills directory creation |
| R8 | Graceful error handling for invalid skills/agents |
| R9 | Skill detection via LLM: classify relevance using skill names, descriptions, and user message |
| R10 | Inject matched skill content (body without frontmatter) and directory path as `<skills>` XML context block |
| R11 | Detected skills persist for the session; on topic shift (new session), detection runs again |
| R12 | Detection quality: balance precision (don't waste context on irrelevant skills) with recall (don't miss applicable skills) |
| R13 | When no skills exist in the registry, provider is a no-op (no context, no agents, no LLM call) |
| R14 | Graceful error handling for detection — failures never block the message; message proceeds with no skills/agents |
| R15 | Base system prompt preamble includes a static Skills section so the agent has foundational awareness of the skill system independent of per-session detection |

## Behaviors

### Skill Organization (R0)

Skills are directory-based packages in `workspace/skills/`. Each skill contains:
- `SKILL.md`: Metadata file with skill name, description, and version (YAML frontmatter)
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
- Given the system prompt is assembled, then the preamble Skills section describes the `skills/` directory structure including `SKILL.md` and `agents/` subdirectory
- Given the preamble Skills section, then it explains per-session detection and contextual injection of relevant skills
- Given the preamble Skills section, then it states the agent can create and manage skills by reading and writing files
- Given the preamble Skills section, then it explicitly distinguishes from Claude Code's native skills and slash commands

### Skill Registry (R1, R2, R3)

The skill registry discovers all skills and agents at startup, building an indexed dictionary.

**Acceptance Criteria**:
- Given the registry initializes, when it scans the skills/ directory, then all valid skills are discovered
- Given a skill with valid SKILL.md, when loaded, then all agents in its agents/ subdirectory are discovered
- Given agents from multiple skills, when indexed, then they are namespaced by skill (e.g., "skill-name/agent-name")
- Given an invalid skill, when the registry encounters it, then a warning is logged and loading continues

### Coordinator Integration (R4, R11)

The coordinator receives detected agents from the pre-processing pipeline per-session and passes them to the SDK.

**Acceptance Criteria**:
- Given the pre-processing pipeline returns results containing agent definitions, when the coordinator processes them, then the detected agents are passed to `ClaudeAgentOptions.agents` for the session
- Given agents are loaded for a session, when subsequent messages arrive in the same session, then the same agents remain available without re-detection
- Given a new session starts after a topic shift, when skill detection runs again, then agents are re-detected based on the new message context

### Tool Scoping (R6)

Agent definitions can specify which tools the agent is allowed to use.

**Acceptance Criteria**:
- Given an agent definition specifies a tools list, when the agent is created, then that constraint is included in the AgentDefinition
- Given an agent definition omits tools, when the agent is created, then the SDK applies default tool access

### Bootstrap (R7)

A bootstrap hook creates the skills directory if missing.

**Acceptance Criteria**:
- Given the bootstrap runs, when the skills hook executes, then the skills/ directory is created if it doesn't exist
- Given the skills directory already exists, when the hook runs again, then no action is taken (idempotent)

### Error Handling (R8)

Invalid skills and agents are gracefully skipped with diagnostic logging.

**Acceptance Criteria**:
- Given a skill is malformed, when the registry loads it, then a warning is logged and other skills load normally
- Given an agent definition is invalid, when loaded, then a warning is logged and the agent is skipped
- Given the registry encounters an error, then the coordinator continues with whatever agents were successfully loaded

### Skill Detection (R9, R12, R13)

On the first message of a new session, the skills context provider classifies which skills are relevant to the user's message.

**Acceptance Criteria**:
- Given skills exist in the registry and a new session starts, when the first message arrives, then the skills context provider classifies relevance using LLM-based analysis with all skill names and descriptions
- Given the classification completes, when the response is parsed, then unrecognized skill names are discarded
- Given no skills exist in the registry, when the provider runs, then it returns immediately with no context and no agents (no LLM call made)

### Skill Content Injection (R10)

Detected skills' content is injected as a `<skills>` XML context block.

**Acceptance Criteria**:
- Given skills are detected as relevant, when the provider assembles the result, then it returns a `<skills>` XML context block containing each matched skill's content body (markdown without YAML frontmatter) and its directory path
- Given multiple skills are detected, when the context block is assembled, then each skill's content is clearly delineated with its name and directory path
- Given no skills are detected as relevant, when the provider completes, then it returns no text context and no agent definitions

### Detection Error Handling (R14)

Detection failures are handled gracefully without blocking the message.

**Acceptance Criteria**:
- Given the skills detection agent fails (SDK error, timeout), when the provider catches the error, then it logs the failure and returns no context and no agents
- Given the detection agent returns an unrecognizable response (no valid skill names parseable), when the provider processes it, then it logs a warning and returns no context and no agents

## Out of Scope

- Custom MCP tools per-agent
- Skill-level markdown instructions
