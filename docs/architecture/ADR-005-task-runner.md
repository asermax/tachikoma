# ADR-005: Task Runner

**Status**: Accepted
**Date**: 2026-03-08

## Context

Development workflows require running multiple commands in sequence or with specific arguments:
- Running tests with various options
- Linting and formatting
- Type checking
- Starting the agent

We need a way to define and run these project-specific commands consistently.

**Requirements:**
- Simple syntax for defining commands
- Cross-platform support (Linux, macOS)
- No runtime dependencies on Python (can run before venv exists)
- Support for command arguments and dependencies between tasks

## Decision

Use **just** as the task runner for development workflows.

just is a Rust-based command runner with Make-like syntax but simpler semantics. Commands are defined in a `justfile` and run with `just <recipe>`.

## Consequences

### Positive

- **Simple syntax**: Make-inspired but without Make's quirks (tabs vs spaces, etc.)
- **Fast**: Written in Rust, starts instantly
- **Cross-platform**: Works on Linux, macOS, and Windows
- **Self-documenting**: `just --list` shows available commands with descriptions
- **Dependency support**: Recipes can depend on other recipes
- **Arguments**: Recipes can accept arguments
- **No Python dependency**: Can run before Python/uv is set up
- **Stable**: Version 1.0+ with backwards compatibility commitment

### Negative

- **External tool**: Requires separate installation (not bundled with Python/uv)
- **Learning curve**: New syntax for team members unfamiliar with just
- **Not Python**: Commands are shell-based, not Python functions

## Alternatives Considered

### Makefile

- **Description**: Traditional Unix build tool
- **Why not chosen**: Complex syntax, tabs-vs-spaces issues, primarily a build system not a task runner

### Shell scripts

- **Description**: Collection of .sh files
- **Why not chosen**: Harder to discover, no dependency management, platform-specific

---

## Notes

**Installation:**
```bash
# Arch Linux
sudo pacman -S just

# macOS
brew install just

# Cargo
cargo install just

# Other: see https://github.com/casey/just#installation
```

**Basic usage:**
```bash
just              # run default recipe
just --list       # show available recipes
just <recipe>     # run specific recipe
just <recipe> arg # run recipe with argument
```

**Documentation**: https://just.systems/man/en/
