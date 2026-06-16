# ADR-001: Agent SDK

**Status**: Accepted
**Date**: 2026-06-11

## Context

Tachikoma needs an agent runtime that is native to TypeScript, embeds in-process, and exposes extension points that match the thin-core + extensions architecture. Subprocess-based runtimes like the Claude Agent SDK impose a per-message subprocess model — a fresh client per exchange with `resume`-based continuity, session forking for post-processing, in-process MCP servers for tools, and workarounds like custom transports to dodge ARG_MAX limits — pushing significant compensating machinery (context persistence and reassembly, transcript archiving, stderr capture) onto the host.

## Decision

Build on the **pi agent SDK** (`@earendil-works/pi-coding-agent`), embedded via `createAgentSession`, with `@earendil-works/pi-ai` for side-channel completions (boundary detection, summaries, extraction). The version is **pinned exact** (currently 0.79.3) and re-verified against the shipped docs and `.d.ts` on every upgrade.

Key properties driving the choice:
- **Long-lived in-process sessions**: one `AgentSession` per conversation, opened via `createAgentSession` + `SessionManager.open` and swapped directly by the coordinator on topic boundary — no per-message client recreation or resume bookkeeping. The SDK's higher-level `AgentSessionRuntime` is **intentionally not used**: it is the interactive-TUI/RPC lifecycle layer, whereas Tachikoma's source of truth is its own drizzle-backed session registry — operational metadata (summary, `closedAt`/`lastResumedAt`, phased `postProcessingState`, channel↔message routing) that the runtime's jsonl model has no place for. The coordinator drives the single-active invariant and the registry drives persistence/resume; see `src/agent/manager.ts` and `src/sessions/registry.ts`.

  > **Superseded by [ADR-014](ADR-014-session-source-of-truth.md)** (the source-of-truth portion only). Under the daily-trunk session model, the pi **session file** is the conversational source of truth: the `sessions` registry is removed, and pi's session-tree primitives (`branchWithSummary`, custom entries, `createBranchedSession` forks, `getBranch`) are adopted. `AgentSessionRuntime` itself remains unused. The rest of this ADR (the pi SDK choice, version pinning, prompt ownership, fork-based extraction) stands.
- **Extension system**: pi extensions (tools via `registerTool`, event hooks, system prompt contributions) map directly onto Tachikoma's `app.agent.use()` surface
- **Tree-structured JSONL transcripts, forked for post-processing**: each session's transcript is a JSONL tree on disk (archived too), and the conversation-aware close-time processors — memory extraction and core-context — **fork** it via `SessionManager.forkFrom`, wrapped by the `AgentManager.forkAndContinue(file, prompt, tier, tools)` primitive. The same assistant continues on the fork with the full conversation live in its history and the composed persona intact, handed a follow-up user instruction and hard-limited to a file-tool allowlist; the source transcript is never mutated. This mirrors the legacy Python implementation's fork-based extraction. Side-channel work that needs no conversation context (boundary classification, rolling summaries, commit messages, nightly store maintenance) still uses standalone `pi-ai` completions / headless side-runs. Transcripts are isolated under a dedicated `agentDir`.
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
- Post-processing forks the JSONL session for conversation-aware work (memory, core-context) and uses one-shot `pi-ai` `complete()` calls for context-free work (summaries, classification, commit messages)
- Multi-provider model registry comes for free (Anthropic primary, others available)

### Negative

- **Pre-1.0 churn**: the API moves fast; mitigated by pinning the exact version and maintaining verified SDK notes (`docs/reference/pi-sdk-notes.md`) that are re-checked on every bump
- **No MCP support, by design**: acceptable — plain `registerTool()` registrations cover every current tool need; connecting to external MCP servers would require new work if ever wanted
- Smaller ecosystem and community than the Claude Agent SDK

## Alternatives Considered

- **Claude Agent SDK (TypeScript)**: the per-message subprocess model described in the Context, with all the accidental complexity it pushes onto the host
- **Raw provider API + custom agent loop**: maximum control, but reimplements sessions, transcripts, compaction, tools, and skills that pi provides and maintains
