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
| [DES-007](DES-007-marker-computed-effective-state.md) | Marker-Computed Effective State over an Append-Only Log | When state must change on the append-only session tree, append a marker naming the target and compute the effective state at read time by scanning markers — one chokepoint serves every consumer |
