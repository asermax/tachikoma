# ADR-002: Runtime and Package Manager

**Status**: Accepted
**Date**: 2026-06-11

## Context

The rewrite needs a Node.js baseline and a dependency manager. pi requires Node >= 22.19. A traditional TypeScript build step (tsc/esbuild watch, dist artifacts, source maps) adds friction for a long-running personal service that is edited and restarted frequently.

## Decision

Target **Node.js >= 22.19** and run TypeScript sources directly via **native type stripping** — no build step in development. `tsconfig.json` enforces the constraints that make this safe:

- `erasableSyntaxOnly`: only type-level syntax allowed (no enums, namespaces, or parameter properties)
- `allowImportingTsExtensions` + `rewriteRelativeImportExtensions`: imports use explicit `.ts` extensions
- `tsc --noEmit` serves purely as the type checker (see quality gates)

Use **pnpm** as the package manager, with the version pinned via the `packageManager` field.

## Consequences

### Positive

- `node src/main.ts` just runs — no compile/watch loop, no stale dist artifacts, instant restart cycle
- `erasableSyntaxOnly` aligns with the project style (constant maps over enums) and guarantees sources behave identically under any TS-aware runner
- pnpm is fast, disk-efficient (content-addressed store), and strict about phantom dependencies

### Negative

- Type stripping is a recent runtime feature; behavior differences across Node minors are possible — mitigated by the engines floor
- Erasable-only forbids a few TS conveniences (enums, namespaces, constructor parameter properties); accepted as consistent with house style
- If a published build is ever needed, the emit path (`rewriteRelativeImportExtensions`, `outDir`) must be exercised then — it is configured but unused day to day

## Alternatives Considered

- **tsx/ts-node loaders**: extra dependency and a second resolution pipeline for what the runtime now does natively
- **Build step (tsc/esbuild)**: reproducible artifacts but constant dev friction; unnecessary for a self-hosted single-deployment service
- **npm/yarn**: workable, but pnpm's strictness and speed won; the lockfile decides for contributors
