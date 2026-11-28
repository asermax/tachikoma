# ADR-002: Runtime and Package Manager

**Status**: Accepted
**Date**: 2026-06-11

## Context

The project needs a Node.js baseline and a dependency manager. pi requires Node >= 22.19. A traditional TypeScript build step (tsc/esbuild watch, dist artifacts, source maps) adds friction for a long-running personal service that is edited and restarted frequently.

## Decision

Target **Node.js >= 22.19** and run TypeScript sources directly via **native type stripping** — no build step in development. `tsconfig.json` enforces the constraints that make this safe:

- `erasableSyntaxOnly`: only type-level syntax allowed (no enums, namespaces, or parameter properties)
- `allowImportingTsExtensions` + `rewriteRelativeImportExtensions`: imports use explicit `.ts` extensions
- `tsc --noEmit` serves purely as the type checker (see quality gates)

Use **pnpm** as the package manager, with the version pinned via the `packageManager` field.

pnpm runs no dependency's build unless the repository decides on it. `allowBuilds` in `pnpm-workspace.yaml` carries one decision for every package pnpm treats as needing a build — one that declares an install script (`preinstall`, `install` or `postinstall`), ships a root `binding.gyp`, or ships files under `.hooks/` — and for no other package. Four are in the tree today: `esbuild` is allowed, because its `postinstall` places the platform binary; `@google/genai`, `protobufjs` and `better-sqlite3` are denied. better-sqlite3 is denied although it ships a `binding.gyp`, because it loads the prebuilt binary it ships. The list has to be exact in both directions: a package in that set without an entry makes pnpm write a placeholder into the workspace file and fail the install with `ERR_PNPM_IGNORED_BUILDS`, and an entry for any other package is an approval nothing asked for.

## Consequences

### Positive

- `node src/main.ts` just runs — no compile/watch loop, no stale dist artifacts, instant restart cycle
- `erasableSyntaxOnly` aligns with the project style (constant maps over enums) and guarantees sources behave identically under any TS-aware runner
- pnpm is fast, disk-efficient (content-addressed store), and strict about phantom dependencies
- One build is approved, and a dependency that starts needing a build after an upgrade stops the install until it is ruled on

### Negative

- Type stripping is a recent runtime feature; behavior differences across Node minors are possible — mitigated by the engines floor
- Erasable-only forbids a few TS conveniences (enums, namespaces, constructor parameter properties); accepted as consistent with house style
- If a published build is ever needed, the emit path (`rewriteRelativeImportExtensions`, `outDir`) must be exercised then — it is configured but unused day to day
- Every dependency upgrade can change the set: a package that newly declares an install script or ships a `binding.gyp` fails the install until `allowBuilds` rules on it, and pnpm has already rewritten the workspace file by then
- A denial is not reviewed again: a future version of a denied package that needs its build fails at load rather than at install, so a load failure after an upgrade is the signal to look at that package's entry again

## Alternatives Considered

- **tsx/ts-node loaders**: extra dependency and a second resolution pipeline for what the runtime now does natively
- **Build step (tsc/esbuild)**: reproducible artifacts but constant dev friction; unnecessary for a self-hosted single-deployment service
- **npm/yarn**: workable, but pnpm's strictness and speed won; the lockfile decides for contributors

## Notes

- Related: [ADR-016](ADR-016-minimum-release-age.md), the other policy kept in `pnpm-workspace.yaml`, and [ADR-004](ADR-004-persistence-layer.md) for better-sqlite3's denied build
- Where the rule was read: pnpm 11.3.0's own distribution (`pkgRequiresBuild` and `filesIncludeInstallScripts` in `dist/worker.js`, `handleIgnoredBuilds` in `installing/commands`) and three probe installs
- Where the Node floor is also load-bearing: `process.execve` (experimental, Stability 1) exists since v22.15.0 and is what replaces the process in place on a `restart_self` / `upgrade_self` and the boot-time rollback (see [self-update](../feature-designs/self-update.md)). A floor below 22.15 would take the re-exec away with it
