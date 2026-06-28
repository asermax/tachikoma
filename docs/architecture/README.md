# Architecture Decisions

Architecture Decision Records (ADRs): one-time, hard-to-reverse choices about the stack and structure. Repeatable patterns live under [../design/](../design/) as DES documents.

| ID | Decision | Status |
|----|----------|--------|
| [ADR-001](ADR-001-agent-sdk.md) | Build on the pi agent SDK (`@earendil-works/pi-coding-agent`), pinned exact, with `pi-ai` for side-channel completions | Accepted |
| [ADR-002](ADR-002-runtime-and-package-manager.md) | Target Node.js >= 22.19 and run TypeScript directly via native type stripping — no dev build step | Accepted |
| [ADR-003](ADR-003-schema-language.md) | Use TypeBox 1.x everywhere as the single schema language | Accepted |
| [ADR-004](ADR-004-persistence-layer.md) | Use drizzle-orm over better-sqlite3, with drizzle-kit for versioned migrations | Accepted |
| [ADR-005](ADR-005-telegram-library.md) | Use grammY for the Telegram channel extension | Accepted |
| [ADR-006](ADR-006-scheduler.md) | Use croner for all in-process scheduling | Accepted |
| [ADR-007](ADR-007-configuration.md) | TOML at `~/.config/tachikoma/config.toml`, parsed with smol-toml and validated with TypeBox | Accepted |
| [ADR-008](ADR-008-logging-library.md) | Use pino for structured logging | Accepted |
| [ADR-009](ADR-009-testing-library.md) | Use vitest as the test framework | Accepted |
| [ADR-010](ADR-010-linting-formatting.md) | Use Biome for both linting and formatting | Accepted |
| [ADR-011](ADR-011-task-runner.md) | Use just as the task runner | Accepted |
| [ADR-012](ADR-012-repository-structure.md) | Single repository, single package: thin core plus all first-party extensions in-tree | Accepted |
| [ADR-013](ADR-013-release-tooling.md) | Use semantic-release run locally (`just release`) against the `master` branch | Accepted |
| [ADR-014](ADR-014-session-source-of-truth.md) | Session file as the conversational source of truth — the daily-trunk model, removing the `sessions` table | Accepted |
| [ADR-015](ADR-015-subagent-extension-tool-grants.md) | Subagent extension-tool grants via a source-agnostic binding mechanism — a `subagent` session scope with per-factory opt-in | Accepted |
