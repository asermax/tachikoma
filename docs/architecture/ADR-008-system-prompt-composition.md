# ADR-008: System Prompt Composition via Append

**Status**: Accepted
**Date**: 2026-03-13
**Deciders**: Team

## Context

The agent needs to be customizable with foundational context (personality, user knowledge, operational guidelines) without losing the Claude Code SDK's built-in behaviors (tool use instructions, safety guidelines, agentic loop logic). The SDK provides a `ClaudeAgentOptions.system_prompt` field that accepts either a plain string or a `SystemPromptPreset` TypedDict.

Two approaches:
1. **Replace** the entire system prompt with custom content (simple but loses SDK behaviors)
2. **Append** custom content to the SDK's default prompt (preserves behaviors but requires using the `SystemPromptPreset` mechanism)

## Decision

Use `SystemPromptPreset(type="preset", preset="claude_code", append=context_string)` to layer foundational context on top of the SDK's default prompt.

## Rationale

- **Preserves SDK defaults**: The SDK's default `claude_code` prompt includes essential tool-use instructions, safety guidelines, and agentic loop behaviors that are necessary for the agent to function correctly. Replacing the entire prompt would require reimplementing all of this.
- **Designed extension point**: The `SystemPromptPreset` with `append` mode is the SDK's intended mechanism for prompt customization, not a workaround.
- **Future-proof**: As the SDK updates its default prompt with improvements or security fixes, those updates automatically apply to Tachikoma without requiring manual rebasing.
- **Clear layering**: The architecture is transparent — the agent uses "the SDK's default behaviors plus our custom context," which is easier to reason about than a monolithic custom prompt.

## Consequences

### Positive
- Agent operates with all built-in SDK behaviors intact
- SDK prompt updates apply automatically
- Custom context is isolated in clearly delimited sections (XML tags: `<soul>`, `<user>`, `<agents>`)
- Extension point is well-defined for future customizations

### Negative
- We don't have direct control over the full system prompt — we must work within the SDK's design
- The final prompt is the concatenation of two independent sources, which could theoretically create conflicts (unlikely in practice)

## Related Decisions

- **Core-context loading**: DLT-005 defines how foundational context files are loaded and assembled
- **SDK library choice**: ADR-007 selected the Claude Agent SDK as the agent runtime

## See Also

- `docs/feature-specs/agent/core-architecture.md` — R9: Foundational context requirement
- `docs/feature-designs/agent/core-architecture.md` — Startup flow: system_prompt passed to Coordinator
- `src/tachikoma/context.py` — Context loading and assembly implementation
- `src/tachikoma/coordinator.py` — SystemPromptPreset wrapping in ClaudeAgentOptions
