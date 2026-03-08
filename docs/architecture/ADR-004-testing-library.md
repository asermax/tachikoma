# ADR-004: Testing Library

**Status**: Accepted
**Date**: 2026-03-08

## Context

Need to establish testing infrastructure for the Tachikoma project.

**Testing approach**:
- Unit tests for domain types, adapter logic, and pure functions
- Integration tests for the coordinator (mocking the SDK subprocess)
- Mock external dependencies (SDK CLI process, filesystem)

**Key requirement**: Fast test execution for quick validation of changes during development.

## Decision

Use **pytest** as the testing framework for all unit and integration tests.

**Plugin stack**:
- `pytest` (core framework)
- `pytest-asyncio` (async test support — the agent is async-first)
- `pytest-mock` (clean mocking syntax)
- `pytest-xdist` (parallel execution)
- `pytest-cov` (coverage reporting)

## Consequences

### Positive

- **Fast test execution**: pytest-xdist enables parallel test execution
- **Clean syntax**: Plain Python `assert` statements instead of verbose assertion methods
- **Rich plugin ecosystem**: 1300+ plugins available for specialized testing needs
- **Excellent mocking**: pytest-mock provides clean syntax for mocking
- **Async support**: pytest-asyncio handles async coordinator and adapter tests
- **Tool integration**: Seamless integration with existing tooling stack (uv, ruff)
- **Powerful fixtures**: Session-scoped fixtures for complex setup/teardown

### Negative

- **External dependency**: Not part of Python standard library
- **Learning curve**: Pytest's fixture auto-discovery and parametrize require familiarity

## Alternatives Considered

### unittest

- **Description**: Python standard library testing framework
- **Why not chosen**: More verbose syntax; less flexible fixture system

---

## Notes

- Install with: `uv add --dev pytest pytest-asyncio pytest-mock pytest-xdist pytest-cov`
- Documentation: https://docs.pytest.org/
- Run with: `uv run pytest`
