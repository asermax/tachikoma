# ADR-001: Package Manager

**Status**: Accepted
**Date**: 2026-03-08

## Context

We need a Python package manager to handle dependency management for the project. Key requirements:
- Fast installation and dependency resolution
- Lock file support for reproducible builds across development and production
- Best-in-class tooling and developer experience
- Reliable dependency management without common issues

The project is starting fresh, so we can choose the best modern tooling without migration concerns.

## Decision

Use **uv** as the package manager for Python dependency management.

All dependencies will be managed through:
- `pyproject.toml` for dependency declarations (PEP 621 standard)
- `uv.lock` for locked, reproducible dependency trees
- `uv` CLI for all package operations (add, remove, install, sync)

## Consequences

### Positive

- **Extremely fast**: 10-100x faster than pip/poetry for installations and dependency resolution (written in Rust)
- **Lock file support**: Generates `uv.lock` for reproducible builds across environments
- **Standards-compliant**: Uses standard `pyproject.toml` format, compatible with PEP 621
- **Modern tooling**: Excellent CLI UX with clear error messages and helpful output
- **Unified tool**: Replaces pip, pip-tools, and virtualenv with a single, cohesive tool
- **Active development**: Rapidly improving, backed by Astral (creators of ruff)
- **Python version management**: Built-in Python version management

### Negative

- **Relatively new**: Less battle-tested than poetry/pip (first stable release in 2024)
- **Smaller community**: Fewer Stack Overflow answers compared to poetry
- **IDE integration**: IDE support is improving but less mature than poetry

## Alternatives Considered

### Poetry

- **Description**: Popular Python dependency manager with lock files and virtual environment management
- **Why not chosen**: Significantly slower than uv for dependency resolution and installation; has known issues with dependency conflicts

### pip (standard)

- **Description**: Default Python package installer, ships with Python
- **Why not chosen**: No built-in lock file support, making reproducible builds difficult

---

## Notes

- Install uv: `curl -LsSf https://astral.sh/uv/install.sh | sh`
- Documentation: https://docs.astral.sh/uv/
- Common commands:
  - `uv add <package>` - Add dependency
  - `uv remove <package>` - Remove dependency
  - `uv sync` - Install dependencies from lock file
  - `uv run <command>` - Run command in virtual environment
