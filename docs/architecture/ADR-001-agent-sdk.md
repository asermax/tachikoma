# ADR-001: Agent SDK

**Status**: Accepted
**Date**: 2026-06-11

## Context

The Python implementation runs on the Claude Agent SDK, which imposes a per-message subprocess model: a fresh client per exchange with `resume`-based continuity, session forking for post-processing, in-process MCP servers for tools, and workarounds like a custom transport to dodge ARG_MAX limits. Much of the codebase exists to compensate for these constraints (context persistence and reassembly, transcript archiving, stderr capture). The rewrite needs an agent runtime that is native to TypeScript, embeds in-process, and exposes extension points that match the thin-core + extensions architecture.

## Decision

Build on the **pi agent SDK** (`@earendil-works/pi-coding-agent`), embedded via `createAgentSession`, with `@earendil-works/pi-ai` for side-channel completions (boundary detection, summaries, extraction). The version is **pinned exact** (currently 0.79.1) and re-verified against the shipped docs and `.d.ts` on every upgrade.

Key properties driving the choice:
- **Long-lived in-process sessions**: one `AgentSession` per conversation, replaced on topic boundary via `AgentSessionRuntime` — no per-message client recreation or resume bookkeeping
- **Extension system**: pi extensions (tools via `registerTool`, event hooks, system prompt contributions) map directly onto Tachikoma's `app.agent.use()` surface
- **Tree-structured JSONL transcripts with fork**: readable on disk for post-session extraction, forkable for advanced flows, isolated under a dedicated `agentDir`
- **Agent Skills standard support**: progressive disclosure replaces the LLM-based skill classifier

## Consequences

### Positive

- Native TypeScript end to end — no subprocess boundary, no transport workarounds, direct access to agent state
- In-process sessions eliminate the context persistence/reassembly layer entirely
- Post-processing simplifies to reading JSONL transcripts plus one-shot `pi-ai` `complete()` calls
- Multi-provider model registry comes for free (Anthropic primary, others available)

### Negative

- **Pre-1.0 churn**: the API moves fast; mitigated by pinning the exact version and maintaining verified SDK notes (`docs/reference/pi-sdk-notes.md`) that are re-checked on every bump
- **No MCP support, by design**: acceptable — in-process MCP servers become plain `registerTool()` registrations, which covers every current need; connecting to external MCP servers would require new work if ever wanted
- Smaller ecosystem and community than the Claude Agent SDK

## Alternatives Considered

- **Claude Agent SDK (TypeScript)**: same per-message subprocess model that generated the accidental complexity the rewrite is shedding
- **Raw provider API + custom agent loop**: maximum control, but reimplements sessions, transcripts, compaction, tools, and skills that pi provides and maintains
