# ADR-007: Configuration

**Status**: Accepted
**Date**: 2026-06-11

## Context

Tachikoma's user-facing configuration contract is TOML at `~/.config/tachikoma/config.toml`, schema-validated, with an auto-generated, commented default file on first run. Config is an implementation artifact, not workspace data, so the schema is free to evolve — notably, extensions declare their own config sections.

## Decision

Keep **TOML at `~/.config/tachikoma/config.toml`**, parsed with **smol-toml** and validated with **TypeBox** schemas (ADR-003).

- Each extension contributes its config schema via `defineExtension({ config })`; the core composes them into the full application schema
- Values are validated and defaulted through the TypeBox schema at startup; invalid config fails fast with precise diagnostics
- A commented default file is generated on first run, documenting every setting
- **No overlap with pi's own configuration**: config.toml owns only what pi has no concept of (workspace, channels, sessions, scheduler, per-role model selection, extension settings). pi-level concerns — default model, thinking budgets, compaction, retry, custom providers, credentials — live in pi's `settings.json`/`models.json`/`auth.json` under `{workspace}/.tachikoma/pi/`. The `[agent]` section is a per-role model map (`main`/`searcher`/`processor`/`classifier`, `provider/model-id[:thinkingLevel]`); unset roles fall back along the role chain and finally to pi's own resolution
- Secrets (API keys, bot tokens) stay in environment variables, not the config file

## Consequences

### Positive

- A familiar format and a conventional location, documented by the generated defaults
- One schema language for config and everything else (ADR-003) — config types are inferred, not duplicated
- smol-toml is small, spec-compliant (TOML 1.0), fast, and dependency-free
- Extension-owned config sections keep the thin core ignorant of feature settings

### Negative

- **Schema changes are not guaranteed backward-compatible** — intentional, but users migrating across breaking changes must reconcile their settings against the newly generated defaults
- smol-toml does not preserve comments through parse/serialize round-trips; programmatic write-back (a future config CLI) will need a comment-preserving strategy rather than naive re-serialization

## Alternatives Considered

- **JSON/YAML config**: loses the established TOML contract; YAML adds parser complexity for no gain
- **@iarna/toml**: unmaintained and TOML 0.5-era
- **Env-only configuration**: fine for secrets, hostile for the ~dozens of tunable settings Tachikoma exposes
