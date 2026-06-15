# ADR-007: Configuration

**Status**: Accepted
**Date**: 2026-06-11

## Context

Tachikoma's user-facing configuration contract is TOML at `~/.config/tachikoma/config.toml`, schema-validated, with an auto-generated, commented default file on first run. Config is an implementation artifact, not workspace data, so the schema is free to evolve — notably, extensions declare their own config sections.

## Decision

Keep **TOML at `~/.config/tachikoma/config.toml`**, parsed with **smol-toml** and validated with **TypeBox** schemas (ADR-003).

- Each extension contributes its config schema via `defineExtension({ config })`; the core composes them into the full application schema
- Values are validated and defaulted through the TypeBox schema at startup; invalid config fails fast with precise diagnostics
- A few values carry constraints TypeBox cannot express and are validated in the load step after schema parsing, still raising the same `ConfigError`: `scheduler.timezone` must be a valid IANA zone (checked via `Intl.DateTimeFormat`); when unset it resolves to the detected system timezone (`Intl.DateTimeFormat().resolvedOptions().timeZone`) so cron schedules are anchored to an explicit zone rather than croner's implicit local time
- A commented default file is generated on first run, documenting every setting
- **No overlap with pi's own configuration**: config.toml owns only what pi has no concept of (workspace, channels, sessions, scheduler, per-role model selection, extension settings). pi-level concerns — default model, thinking budgets, compaction, retry, custom providers, credentials — live in pi's `settings.json`/`models.json`/`auth.json` under `{workspace}/.tachikoma/pi/`. The `[agent]` section is a per-role model map (`main`/`searcher`/`processor`/`classifier`, `provider/model-id[:thinkingLevel]`); unset roles fall back along the role chain and finally to pi's own resolution
- Secrets (API keys, bot tokens) stay in environment variables, not the config file. As a convenience, config may *define* environment variables via an `[env]` section (`string → string`); these are written to `process.env` once at startup (after legacy `adaptConfig`, before any runtime service or pi session is built) so they reach the whole app and everything that inherits the process environment — sessions, spawned tools, detached processes. Config-defined values overwrite existing same-named variables (the config is treated as the explicit source of truth for the keys it names). This is opt-in centralization for env that is convenient to keep alongside the rest of the config; genuine secrets are still better left in the real environment

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
