# Design: Release Pipeline

<!-- This design describes the current implementation approach. Updated through delta reconciliation. -->

**Feature Spec**: [../../feature-specs/distribution/release-pipeline.md](../../feature-specs/distribution/release-pipeline.md)
**Status**: Current

## Purpose

This document explains the design rationale for the release pipeline: the CI/CD workflow structure, versioning automation, publishing approach, and artifact management.

## Problem Context

Tachikoma needs an automated release pipeline that takes code from push-to-master to published-on-PyPI without manual intervention. The pipeline must enforce quality gates, determine versions from commit history, and publish securely.

**Constraints:**
- The project uses uv as its package manager (ADR-001) and `uv_build` as the build backend
- PyPI publishing should use OIDC trusted publisher (no stored API tokens)
- Conventional commits are the version signal (ADR-010)
- Pre-1.0: breaking changes bump minor, not major (`major_on_zero = false`)

**Interactions:**
- Core architecture (core-architecture): `uv_build` build system and `[project.scripts]` entry point
- Quality toolchain: ruff (ADR-002), ty (ADR-003), pytest (ADR-004)

## Design Overview

A two-job GitHub Actions workflow triggered on push to master. The **quality** job runs lint, typecheck, and tests. The **release** job (gated on quality) uses python-semantic-release (ADR-010) to analyze commits, bump the version, and create a GitHub Release. Conditional steps then build the package and publish to PyPI.

```
push to master
  │
  ▼
┌─────────────────────────────────────┐
│  quality job                        │
│  checkout → uv setup → uv sync     │
│  → ruff check → ty check → pytest  │
└──────────────┬──────────────────────┘
               │ needs: quality
               ▼
┌─────────────────────────────────────┐
│  release job                        │
│  checkout (fetch-depth: 0)          │
│  → uv setup → uv sync              │
│  → PSR action (version/tag/release) │
│     │                               │
│     ├─ released == 'true':          │
│     │  → uv build                   │
│     │  → PyPI publish (OIDC)        │
│     │  → attach dist to GH Release  │
│     │                               │
│     └─ released == 'false':         │
│        → skip (no release needed)   │
└─────────────────────────────────────┘
```

## Components

### Implementation Structure

| Layer/Component | Responsibility | Key Decisions |
|-----------------|----------------|---------------|
| `.github/workflows/release.yml` | Two-job CD workflow: quality gates → release + publish | Triggered on `push` to `master`; concurrency group `release` with `cancel-in-progress: false`; default permissions `contents: read`, release job elevates to `contents: write` + `id-token: write` |
| `pyproject.toml` `[tool.semantic_release]` | PSR configuration: commit parser, version location, build command, changelog mode | `commit_parser = "conventional"` (not deprecated `"angular"`); `major_on_zero = false`; `tag_format = "v{version}"`; changelog `mode = "update"` (prepends, preserves existing) |
| `pyproject.toml` `[build-system]` | Build backend configuration | `uv_build` backend pinned to `>=0.11.2,<0.12.0`; auto-includes non-Python files in package directory |
| `CHANGELOG.md` | Changelog file maintained by PSR | Seed file with Keep a Changelog header; PSR prepends new entries on each release |

### Cross-Layer Contracts

**PSR action → conditional steps:**

The PSR GitHub Action (`python-semantic-release/python-semantic-release@v10.2.0`) produces outputs that gate downstream steps:
- `steps.release.outputs.released`: `'true'` if a version bump occurred, `'false'` otherwise
- `steps.release.outputs.tag`: the created tag (e.g., `v0.2.0`) — used by the publish-action to attach dist to the correct GitHub Release

**PSR build command:**

PSR's `build_command = "uv lock && uv build"` runs during the version bump step:
1. `uv lock` regenerates `uv.lock` with the new version
2. `uv build` produces wheel + sdist in `dist/`
3. Both are committed alongside the version stamp and changelog update

## Data Flow

### Release flow

```
1. Push to master triggers workflow
2. quality job:
   a. checkout → astral-sh/setup-uv → uv sync --all-groups
   b. ruff check . → ty check → pytest
   c. If any step fails → job fails, release job skipped
3. release job (needs: quality):
   a. checkout with fetch-depth: 0 (full history for PSR commit analysis)
   b. astral-sh/setup-uv → uv sync --all-groups
   c. PSR action analyzes commits since last tag:
      - feat: → minor bump
      - fix:/perf: → patch bump
      - BREAKING CHANGE footer → minor bump (major_on_zero = false)
      - docs:/chore: only → no release (released = 'false')
   d. If releasing:
      - Stamps pyproject.toml:project.version
      - Runs build_command: uv lock && uv build
      - Updates CHANGELOG.md (prepends new entry)
      - Commits version + lock + changelog + dist
      - Creates git tag (v{version})
      - Pushes to origin
      - Creates GitHub Release with changelog as notes
   e. If released == 'true':
      - uv build (produces wheel + sdist)
      - pypa/gh-action-pypi-publish (OIDC upload to PyPI)
      - python-semantic-release/publish-action (attaches dist/ to GH Release)
   f. If released == 'false':
      - All conditional steps skipped, job succeeds with no artifacts
```

### Concurrency behavior

```
Push A to master → triggers workflow run A
Push B to master (seconds later) → triggers workflow run B
  → concurrency group "release" with cancel-in-progress: false
  → Run B queues behind Run A
  → Run A completes → Run B starts
  → Run B sees Run A's new tag in history, analyzes only newer commits
```

## Key Decisions

### Two-job workflow (quality → release)

**Choice**: Separate quality and release into distinct jobs with `needs: quality`
**Why**: Quality gates must pass before any release activity. A single job would continue executing release steps even if tests are still running. The `needs` dependency creates a hard gate.
**Consequences**:
- Pro: Clear separation of concerns
- Pro: Quality failure prevents any release activity
- Con: Additional checkout + setup overhead in the release job

### PSR v10.x with conventional commit parser

See ADR-010 for the full decision rationale.

### OIDC trusted publisher (no API tokens)

**Choice**: Use `pypa/gh-action-pypi-publish` with OIDC authentication instead of stored API tokens
**Why**: OIDC exchanges short-lived tokens (15-minute) between GitHub Actions and PyPI. No long-lived secrets to manage or rotate. This is PyPI's recommended approach.
**Alternatives Considered**:
- API token in GitHub secrets: Requires manual rotation, risk of exposure
- `twine upload` with credentials: Legacy, no OIDC support
**Consequences**:
- Pro: No stored secrets in repository
- Pro: Token automatically scoped to the specific workflow/environment
- Con: Requires one-time PyPI-side setup (pending publisher registration)
- Con: Does not work from reusable workflows (`workflow_call`)

### uv_build as build backend

**Choice**: `uv_build` (version-pinned `>=0.11.2,<0.12.0`) as the `[build-system]` backend
**Why**: The project uses uv for dependency management (ADR-001). Using uv's native build backend avoids introducing a separate build tool. Non-Python data files in the package directory (`.md` files in `tachikoma/skills/builtin/`) are auto-included without extra configuration.
**Alternatives Considered**:
- hatchling: Popular modern backend, but adds a dependency outside the uv ecosystem
- setuptools: Legacy, requires more boilerplate
**Consequences**:
- Pro: No extra build-time dependencies beyond uv
- Pro: Data files auto-included (no `package_data` or `MANIFEST.in`)
- Con: Version pin must track uv's release cadence

### Concurrency group with cancel-in-progress: false

**Choice**: Use `concurrency: { group: release, cancel-in-progress: false }`
**Why**: Cancelling an in-progress release could leave a half-tagged, half-published state. Queuing ensures each release completes fully before the next begins.
**Consequences**:
- Pro: No race conditions in version determination or tag creation
- Pro: No half-finished releases
- Con: Rapid successive pushes queue rather than cancel stale runs

## System Behavior

### Scenario: Feature commit triggers release

**Given**: A `feat: add new capability` commit is pushed to master
**When**: The CD workflow runs
**Then**: Quality gates pass, PSR determines a minor bump (e.g., 0.1.0 → 0.2.0), stamps pyproject.toml, updates CHANGELOG.md, creates tag `v0.2.0`, creates GitHub Release, builds wheel, publishes to PyPI, attaches dist to release.

### Scenario: Non-releasable commit

**Given**: A `docs: update README` commit is pushed to master
**When**: The CD workflow runs
**Then**: Quality gates pass. PSR determines no version bump is needed (`released == 'false'`). Build, publish, and release upload steps are skipped. Job succeeds with no artifacts.

### Scenario: Quality gate failure

**Given**: A commit that breaks a test is pushed to master
**When**: The quality job runs
**Then**: pytest fails. The release job never executes. No version bump, no publish.

### Scenario: PyPI publish failure

**Given**: PSR successfully creates a version tag and GitHub Release, but PyPI upload fails
**When**: The publish step fails
**Then**: The GitHub Release still exists with the correct tag and changelog. The publish step can be retried by re-running the failed workflow job.

## Notes

- python-semantic-release docs: https://python-semantic-release.readthedocs.io/en/latest/
- pypa/gh-action-pypi-publish: https://github.com/pypa/gh-action-pypi-publish
- PyPI trusted publisher requires a one-time manual setup at https://pypi.org/manage/account/publishing/ before the first release
- The `uv_build` version pin was updated from the design's original `>=0.10.9,<0.11.0` to `>=0.11.2,<0.12.0` during implementation to match the installed uv version
- `license = {text = "MIT"}` (PEP 639 inline text) is used rather than `license = {file = "LICENSE"}` — the inline form is simpler and the LICENSE file at root serves as the legal artifact
