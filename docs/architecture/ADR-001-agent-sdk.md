# ADR-001: Agent SDK

**Status**: Accepted
**Date**: 2026-06-11

## Context

Tachikoma needs an agent runtime that is native to TypeScript, embeds in-process, and exposes extension points that match the thin-core + extensions architecture. Subprocess-based runtimes like the Claude Agent SDK impose a per-message subprocess model — a fresh client per exchange with `resume`-based continuity, session forking for post-processing, in-process MCP servers for tools, and workarounds like custom transports to dodge ARG_MAX limits — pushing significant compensating machinery (context persistence and reassembly, transcript archiving, stderr capture) onto the host.

## Decision

Build on the **pi agent SDK** (`@earendil-works/pi-coding-agent`), embedded via `createAgentSession`, with `@earendil-works/pi-ai` for side-channel completions (boundary detection, summaries, extraction). The version is **pinned exact** (currently 0.79.1) and re-verified against the shipped docs and `.d.ts` on every upgrade.

Key properties driving the choice:
- **Long-lived in-process sessions**: one `AgentSession` per conversation, replaced on topic boundary via `AgentSessionRuntime` — no per-message client recreation or resume bookkeeping
- **Extension system**: pi extensions (tools via `registerTool`, event hooks, system prompt contributions) map directly onto Tachikoma's `app.agent.use()` surface
- **Tree-structured JSONL transcripts with fork**: readable on disk for post-session extraction, forkable for advanced flows, isolated under a dedicated `agentDir`
- **Agent Skills standard support**: progressive disclosure covers skill detection without a separate LLM classification pass

We embed pi, but we do not inherit its persona: **every execution context fully overrides pi's
native coding-agent base prompt** (via `systemPromptOverride`), because Tachikoma is a personal
assistant, not a coding agent. The operational hygiene Tachikoma wants is **reproduced in its own
source** (`src/agent/prompts.ts`, see [DES-005](../design/DES-005-base-prompt-ownership.md)) rather
than inherited from the operator's global pi append (`~/.pi/agent/APPEND_SYSTEM.md`), so a deployment
never depends on the operator's personal pi config. Delegated subagents additionally suppress pi's
append, project context files, and skills catalog (the `isolatePrompt` flag) so their prompt is
exactly Tachikoma's own.

## Consequences

### Positive

- Native TypeScript end to end — no subprocess boundary, no transport workarounds, direct access to agent state
- In-process sessions mean no context persistence/reassembly layer is needed at all
- Post-processing simplifies to reading JSONL transcripts plus one-shot `pi-ai` `complete()` calls
- Multi-provider model registry comes for free (Anthropic primary, others available)

### Negative

- **Pre-1.0 churn**: the API moves fast; mitigated by pinning the exact version and maintaining verified SDK notes (`docs/reference/pi-sdk-notes.md`) that are re-checked on every bump
- **No MCP support, by design**: acceptable — plain `registerTool()` registrations cover every current tool need; connecting to external MCP servers would require new work if ever wanted
- Smaller ecosystem and community than the Claude Agent SDK

## Alternatives Considered

- **Claude Agent SDK (TypeScript)**: the per-message subprocess model described in the Context, with all the accidental complexity it pushes onto the host
- **Raw provider API + custom agent loop**: maximum control, but reimplements sessions, transcripts, compaction, tools, and skills that pi provides and maintains
