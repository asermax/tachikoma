# ADR-013: Release Tooling

**Status**: Accepted
**Date**: 2026-06-12

## Context

The Python implementation used python-semantic-release: conventional commits drove version bumps, changelog generation, and tagged releases. The rewrite needs an equivalent so DLT-044 carries over, but the project is a single-user, single-package repo that is run from sources — there is no CI pipeline, no npm publishing, and no remote-driven release flow to integrate with.

## Decision

Use **commit-and-tag-version** (the maintained fork of standard-version), run locally via `just release`.

- Versioning and changelog rules live in `.versionrc.json`: conventionalcommits preset, `CHANGELOG.md` generation, with `feat`/`fix`/`perf`/`refactor` surfaced as changelog sections
- A release is one local command: it derives the next semver from commit history, bumps `package.json`, rewrites `CHANGELOG.md`, commits, and tags
- Pushing the release commit and tag stays a deliberate, separate step

## Consequences

### Positive

- Local-first: a release needs no CI, no remote tokens, and no network — matching how the project is actually operated
- Same conventional-commit contract as the Python repo, so commit discipline transfers unchanged
- Dry-run support (`just release --dry-run`) shows the computed bump and changelog before anything is written

### Negative

- Releases are manual; nothing enforces that a release is cut after merging notable work
- The tool is in maintenance-oriented stewardship (fork of the deprecated standard-version) — a future swap may be needed, though the conventional-commit history keeps us portable

## Alternatives Considered

- **semantic-release**: the closest analogue to python-semantic-release, but designed around CI: it refuses interactive local use, requires remote/token configuration, and centers on publishing — all machinery this repo doesn't have or need
- **changesets**: oriented at monorepos and npm package publishing with explicit changeset files per change; heavier ceremony than conventional commits for a single private package
