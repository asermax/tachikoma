# Release Pipeline

<!-- This spec describes the current system capability. Updated through delta reconciliation. -->

## Overview

A GitHub Actions CD pipeline that runs quality gates on every push to master, automatically determines the next version from conventional commits, builds the package, publishes to PyPI via OIDC trusted publisher, and creates a GitHub Release with attached artifacts. Zero-touch releases from commit to published package.

## User Stories

- As a maintainer, I want releases published automatically from conventional commits so that I don't manage versions manually
- As a user, I want to install the latest release via `uv tool install tachikoma` so that I get the most recent version from PyPI

## Requirements

| ID | Requirement |
|----|-------------|
| R0 | Automated semantic versioning from conventional commits (feat → minor, fix → patch, BREAKING CHANGE → minor while pre-1.0) |
| R1 | Quality gates (lint, typecheck, test) must pass before any release is created |
| R2 | PyPI publishing via OIDC trusted publisher — no stored API tokens |
| R3 | GitHub Release created with changelog and dist artifacts attached |
| R4 | CHANGELOG.md updated automatically on each release |
| R5 | Concurrency control: only one release workflow runs at a time |
| R6 | Non-releasable commits (docs, chore) do not trigger a release |
| R7 | Package built with uv_build producing wheel and sdist |

## Behaviors

### Semantic Versioning (R0)

Version bumps are determined automatically from commit messages following the conventional commits specification.

**Acceptance Criteria**:
- Given a push to master with a `feat:` commit, when the CD workflow runs, then a new minor version is created (e.g., 0.1.0 → 0.2.0)
- Given a push to master with a `fix:` commit, when the CD workflow runs, then a new patch version is created (e.g., 0.2.0 → 0.2.1)
- Given a push to master with a `BREAKING CHANGE` footer, when the CD workflow runs, then a minor version bump occurs (`major_on_zero = false`)
- Given a version bump is triggered, when the workflow runs, then pyproject.toml is stamped with the new version, a git tag is created (`v{version}`), and the tag is pushed

### Quality Gates (R1)

All quality checks must pass before the release job can run.

**Acceptance Criteria**:
- Given a push to master, when the CD workflow runs, then lint (`ruff check`), typecheck (`ty check`), and tests (`pytest`) execute first
- Given a quality gate failure, when the quality job fails, then the release job does not execute and no release is created
- Given all quality gates pass, when the quality job succeeds, then the release job proceeds

### PyPI Publishing (R2)

Package publishing uses OIDC trusted publisher with short-lived tokens, no stored API secrets.

**Acceptance Criteria**:
- Given a version bump succeeded and a release was created, when the publish step runs, then the package is uploaded to PyPI via `pypa/gh-action-pypi-publish` with OIDC authentication
- Given a version bump succeeded but the PyPI publish step fails, then the GitHub Release still exists and the publish can be retried by re-running the failed job

### GitHub Release (R3)

A GitHub Release is created for each version with changelog notes and distribution artifacts.

**Acceptance Criteria**:
- Given a version bump is triggered, when PSR creates a release, then a GitHub Release is created with the changelog entry as release notes
- Given a successful build, when the publish step completes, then wheel and sdist files are attached to the GitHub Release

### Changelog (R4)

CHANGELOG.md is updated automatically with each release in update mode (prepends new entries, preserves existing content).

**Acceptance Criteria**:
- Given a version bump is triggered, when the workflow runs, then CHANGELOG.md is updated with the new release's changes
- Given an existing CHANGELOG.md, when a new release is created, then the new entry is prepended without modifying existing entries

### Concurrency Control (R5)

Only one release workflow runs at a time to prevent race conditions in version determination.

**Acceptance Criteria**:
- Given two pushes to master happen in quick succession, when both trigger the CD workflow, then only one runs at a time (the second queues behind the first)

### Non-Releasable Commits (R6)

Commits that don't warrant a release are handled gracefully.

**Acceptance Criteria**:
- Given a push to master with only `docs:` or `chore:` commits, when the CD workflow runs, then no release is created and no publish occurs
- Given no version bump is needed, when PSR completes analysis, then the build, publish, and release upload steps are skipped

### Package Build (R7)

The package is built using `uv build` with the `uv_build` backend.

**Acceptance Criteria**:
- Given a version bump is triggered, when `uv build` runs, then a wheel and sdist are produced in `dist/`
- Given non-Python data files exist in the package directory (`.md` files in `tachikoma/skills/builtin/`), when the wheel is built, then they are included automatically

## Requires

Dependencies:
- None

Assumes existing:
- pyproject.toml with uv_build build system and [project.scripts] entry point (core-architecture R1)
- Conventional commit discipline from contributors
