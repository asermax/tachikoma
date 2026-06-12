# ADR-010: Linting and Formatting

**Status**: Accepted
**Date**: 2026-06-11

## Context

The Python implementation used ruff — one fast tool for both linting and formatting, one config file, one quality-gate command. The rewrite wants the same shape in the TypeScript ecosystem, where the conventional answer (ESLint + Prettier + a stack of plugins and peer dependencies) is notoriously heavy and slow.

## Decision

Use **Biome** for both linting and formatting.

- Single `biome.json` configuration
- `biome check .` as the lint gate, `biome check --write .` as the formatter (wired as `just lint` / `just fmt`)
- Recommended rule set as the baseline, adjusted only when a rule fights the house style

## Consequences

### Positive

- The ruff analogue: one Rust-fast binary replaces ESLint + Prettier + plugin chains; whole-repo checks are effectively instant
- One config file, no plugin/peer-dependency matrix to maintain
- Formatter and linter never disagree (a classic ESLint/Prettier failure mode)
- Import organizing built in

### Negative

- Smaller rule ecosystem than ESLint — in particular, no equivalent for some type-aware rules from typescript-eslint; `tsc --noEmit` (strict mode) covers the type-level ground
- Opinionated formatter with limited knobs; accepted, as with ruff format
- No third-party plugin system yet, so project-specific custom rules aren't an option

## Alternatives Considered

- **ESLint + Prettier**: maximum rule coverage, but two tools, slow runs, and a config/plugin surface that demands ongoing maintenance
- **oxlint**: even faster linting, but no formatter and a younger rule set — would still need a second tool
