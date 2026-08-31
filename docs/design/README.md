# Design Patterns

Design Pattern documents (DES): repeatable, prescriptive patterns applied across the codebase to keep features consistent. One-time stack and structure choices live under [../architecture/](../architecture/) as ADRs.

| ID | Pattern | Description |
|----|---------|-------------|
| [DES-001](DES-001-unified-extension-api.md) | Unified Extension API | The core is only the main loops; every feature ships as an extension consuming services through the `AppContext` |
| [DES-002](DES-002-extension-authoring.md) | Extension Authoring Conventions | The mechanical conventions every extension follows so they look alike and pass the quality gates |
| [DES-003](DES-003-testing-conventions.md) | Testing Conventions | vitest over TS sources, mirrored test structure with AC traceability, structural fakes, real SQLite/git/process in temp dirs |
| [DES-004](DES-004-logging-conventions.md) | Logging Conventions | All components log through one pino root logger; channels own stdout; user-facing progress goes through `app.status()` |
| [DES-005](DES-005-base-prompt-ownership.md) | Base Prompt Ownership | Prompts that replace pi's coding-agent base live in core `src/agent/prompts.ts` with a shared `OPERATIONAL_GUIDANCE`; feature-local side-run task prompts stay inline |
| [DES-006](DES-006-state-based-migration-detection.md) | State-Based, Marker-Free Migration Detection | One-time workspace adaptations detect remaining work by pre-migration state presence, run idempotently, and write no completion marker — the resulting state is the done signal |
| [DES-007](DES-007-debounced-background-task.md) | Trailing-Edge Debounced Background Task | An expensive per-event background job runs once after a burst settles via a resettable debounce timer — single-flight with coalescing, clear/drain, disabled when the delay is 0 |
| [DES-008](DES-008-marker-computed-effective-state.md) | Marker-Computed Effective State over an Append-Only Log | When state must change on the append-only session tree, append a marker naming the target and compute the effective state at read time by scanning markers — one chokepoint serves every consumer |
| [DES-009](DES-009-turn-scoped-anchored-header.md) | Turn-Scoped Anchored-Prefix Header Recomposed by the Renderer | A per-exchange descriptor that must survive in-place edit streaming is anchored as a prefix the renderer recomposes on every edit, then dropped after the exchange |
| [DES-010](DES-010-agent-driven-git-host-verified.md) | Agent-Driven Git, Host-Verified | Agents drive git through a scoped, allowlisted custom tool; the host decides outcomes from git state, never from the agent's self-report |
| [DES-011](DES-011-post-processing-agent-shapes.md) | Post-Processing Agent Shapes | Conversation-aware work forks the session; context-free work runs headless over host-assembled prompts with scoped tools — selected by one question: does the work need the live conversation? |
| [DES-012](DES-012-namespace-sweep-cleanup.md) | Namespace Sweep over Tracked Creation | Throwaway resources in a feature-owned namespace are recovered by sweeping the namespace from a `finally`, not by tracking what was created |
| [DES-013](DES-013-markdown-wiki-store.md) | Markdown Wiki-Store Conventions | Feature-owned knowledge as wiki-style markdown: a one-line index, one page per topic updated-not-duplicated, size caps, empty-then-sweep deletion, host-written ledgers for deterministic facts |
| [DES-014](DES-014-two-tier-agent-facing-documentation.md) | Two-Tier Agent-Facing Feature Documentation | Agent-facing guidance is lean inline (core substrate block plus one usage section per extension, once per session) with read-on-demand reference pages beside the owning module — bounded by a size budget and drift guards |
