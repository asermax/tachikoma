# Skill System and Sub-Agent Delegation

<!-- This spec describes the current system capability. Updated through delta reconciliation. -->

## Overview

The skill system provides a structured way to organize and manage specialized sub-agents. Skills are directory-based packages containing YAML-formatted agent definitions. A skill registry discovers all skills at startup and makes them available to the coordinator, which passes them to the Claude Code SDK for delegation. The SDK's internal orchestrator uses the available agents to decide when and how to delegate specialized work.

## User Stories

- As the system, I need a way to organize sub-agents into reusable skill packages so that specialized work can be delegated to focused agents
- As a skill developer, I want a clear directory structure and format so that I can define agents without coupling to the core system
- As the SDK, I need all available agents to be known at session initialization so that I can make delegation decisions throughout the conversation

## Requirements

| ID | Requirement |
|----|-------------|
| R0 | Skill directory structure (SKILL.md + agents/ subdirectory) |
| R1 | Skill registry discovery at startup |
| R2 | Agent definition loading from markdown files with YAML metadata |
| R3 | Agent namespacing to prevent collisions |
| R4 | Agents passed to SDK at initialization |
| R5 | Session-lifetime agent persistence |
| R6 | Tool scoping via agent definition metadata |
| R7 | Bootstrap hook for idempotent skills directory creation |
| R8 | Graceful error handling for invalid skills/agents |

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

### Skill Registry (R1, R2, R3)

The skill registry discovers all skills and agents at startup, building an indexed dictionary.

**Acceptance Criteria**:
- Given the registry initializes, when it scans the skills/ directory, then all valid skills are discovered
- Given a skill with valid SKILL.md, when loaded, then all agents in its agents/ subdirectory are discovered
- Given agents from multiple skills, when indexed, then they are namespaced by skill (e.g., "skill-name/agent-name")
- Given an invalid skill, when the registry encounters it, then a warning is logged and loading continues

### Coordinator Integration (R4, R5)

The coordinator retrieves the agents dictionary from the registry and passes it to the SDK.

**Acceptance Criteria**:
- Given the coordinator initializes, when it requests agents from the registry, then all discovered agents are available
- Given agents passed to the SDK at initialization, when a conversation is active, then the same agents remain available (no mid-session updates)

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

## Out of Scope

- Automatic skill detection and injection (DLT-021)
- Custom MCP tools per-agent
- Skill-level markdown instructions
