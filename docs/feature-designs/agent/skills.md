# Design: Skill System and Sub-Agent Delegation

<!-- This design describes the current implementation approach. Updated through delta reconciliation. -->

**Feature Spec**: [../feature-specs/agent/skills.md](../../feature-specs/agent/skills.md)
**Status**: Current

## Purpose

The design establishes how the skill system is structured, discovered, and integrated with the coordinator and SDK.

## Problem Context

The coordinator needs to make specialized sub-agents available to the SDK's orchestrator for delegation. Skills provide a structured, discoverable way to organize and define these agents.

## Design Overview

**Skill Directory Structure**:
```
workspace/skills/
├── skill-name/
│   ├── SKILL.md           # metadata: name, description, version
│   └── agents/
│       ├── agent-1.md     # agent definition with frontmatter
│       └── agent-2.md
└── another-skill/
    ├── SKILL.md
    └── agents/
```

**Skill Registry**:
- Scans `workspace/skills/` at startup
- Loads each `SKILL.md` and validates metadata
- For each skill, discovers agent definitions from `agents/` subdirectory
- Builds agents dictionary indexed by namespace: `skill-name/agent-name`
- Returns agents dictionary to coordinator

**Coordinator Integration**:
- Receives agents dictionary from registry
- Passes it to `ClaudeAgentOptions.agents` during SDK initialization
- Agents persist for the session lifetime

**Bootstrap**:
- Skills hook creates `workspace/skills/` if missing
- Executes in bootstrap sequence before coordinator initialization

## Key Decisions

### Directory-based Skills

Skills are directories (not single files), allowing future expansion with additional skill components without breaking the structure.

### Agents Passed to SDK at Initialization

All agents are discovered at startup and passed to the SDK. The SDK's orchestrator handles delegation decisions. Agents cannot be dynamically added or removed mid-session.

### Graceful Error Handling

Invalid skills/agents are logged as warnings; the system continues with whatever agents loaded successfully.

## Components

| Component | Responsibility |
|-----------|-----------------|
| `SkillRegistry` | Discovers and loads all skills and agents at startup |
| Coordinator | Requests agents from registry, passes to SDK |
| Skills bootstrap hook | Creates skills/ directory if missing (idempotent) |
| SDK orchestrator | Decides when/how to delegate to loaded agents (opaque to application) |

## Data Flow

```
1. Bootstrap runs skills hook → creates workspace/skills/ if missing
2. SkillRegistry initializes → scans skills/, loads SKILL.md, discovers agents
3. Coordinator initializes → requests agents from registry
4. Coordinator passes agents to SDK → agents available for session
5. SDK orchestrator delegates to agents during conversation
```

## Integration Points

- **Coordinator**: Depends on SkillRegistry for agents dictionary
- **Bootstrap**: Creates skills directory via hook
- **SDK**: Receives agents, manages delegation
- **Future DLT-021**: Will layer automatic detection on top

## Notes

- The SDK orchestrator makes delegation decisions opaquely. The application provides agents; the SDK decides how to use them.
- Tool scoping via agent definition's tools field is enforced by the SDK at invocation time.
- This design is infrastructure-focused. Intelligence for automatic skill detection (DLT-021) builds on top.
