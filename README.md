# Tachikoma

Tachikoma is a proactive personal assistant built on the [pi agent SDK](https://github.com/earendil-works/pi). It maintains persistent memory across conversations, extracts learnings automatically, handles background tasks during idle time, and is reachable through Telegram or a local REPL.

This is the TypeScript implementation. The core is deliberately thin — config, database, scheduler, channels, session orchestration, and an extension host — and **every feature is an extension**: memory, conversation boundary detection, skills, workflows, scheduled tasks, project tracking, git-versioned workspace, Telegram, detached process supervision, notifications, and third-party plugin loading.

## Requirements

- Node.js >= 22.19 (the project runs TypeScript sources directly via native type stripping)
- pnpm
- LLM provider credentials: an existing [pi](https://pi.dev) login (`~/.pi/agent/auth.json`) is picked up automatically, or set a provider key like `ANTHROPIC_API_KEY` in the environment

## Quick start

```bash
just install            # pnpm install
just run                # start the agent (REPL channel)
just run -c telegram    # start with the Telegram channel
```

On first run a commented config file is generated at `~/.config/tachikoma/config.toml` and the workspace at `~/tachikoma` is initialized (SOUL.md, USER.md, memory layout, git repo).

## Configuration

TOML at `~/.config/tachikoma/config.toml`. Core sections: `[workspace]`, `[agent]` (model tiers as `provider/model-id`), `[logging]`, `[channels]`, `[sessions]`, `[scheduler]`. Each extension reads its own `[extensions.<name>]` section — see the generated file and the feature specs in `docs/feature-specs/`.

## Development

```bash
just check       # lint + typecheck + tests (run before considering anything done)
just test        # vitest
just lint        # biome check
just fmt         # biome check --write
just typecheck   # tsc --noEmit
```

Database migrations: schema lives in `src/db/schema.ts` (aggregating per-extension `schema.ts` modules); generate migrations with `pnpm drizzle-kit generate` — they apply automatically at startup.

## Architecture

```
Channel (telegram/repl extension)
  → Coordinator (core): inbound middleware → session ensure/resume
    → context providers (parallel) → pi AgentSession prompt
    → AgentEvents streamed back to the channel
  → exchange processors (rolling summary, …)
  → on idle close: post-processing phases (memory extraction, git commit, …)
```

- **Extensions** implement features through one contract (`defineExtension`) with app-level hooks (scheduler, db, sessions, channels) and pi-native session hooks (`app.agent.use((pi) => …)`). Start at `docs/design/DES-001-unified-extension-api.md` and `DES-002-extension-authoring.md`.
- **pi SDK ground truth** for this codebase: `docs/reference/pi-sdk-notes.md`.
- **Planning docs**: `docs/planning/VISION.md`, `docs/planning/DELTAS.md`; decisions in `docs/architecture/ADR-*`; feature documentation under `docs/feature-specs/` and `docs/feature-designs/`.

The agent's pi state (sessions, settings) lives isolated under `{workspace}/.tachikoma/pi`, so it never interferes with a personal pi installation; provider credentials are shared from the machine-level pi login when present.
