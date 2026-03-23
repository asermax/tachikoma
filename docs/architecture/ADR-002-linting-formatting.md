# ADR-002: Linting and Formatting

**Status**: Accepted
**Date**: 2026-03-08

## Context

We need linting and formatting tools to maintain consistent code style and catch potential issues early in development. Key requirements:
- Fast linting that doesn't slow down the development workflow
- Consistent code style across the entire project
- Replace multiple separate tools with a unified solution
- Auto-fix capabilities to reduce manual formatting work

## Decision

Use **ruff** for all linting and formatting tasks.

Ruff will handle:
- Code formatting (replaces black)
- Linting and error detection (replaces flake8, pyflakes, pycodestyle)
- Import sorting (replaces isort)
- Code modernization (replaces pyupgrade)

### Rule Configuration

**Enabled rule sets:**
- **C4** (flake8-comprehensions) - Comprehension improvements
- **E, W** (pycodestyle) - PEP 8 style violations and warnings
- **ERA** (eradicate) - Removes commented out code
- **F** (pyflakes) - Logical errors, unused imports
- **I** (isort) - Import sorting
- **PLC** (pylint) - Pylint convention checks
- **SIM** (flake8-simplify) - Code simplification suggestions
- **TID** (tidy-imports) - Import tidiness
- **UP** (pyupgrade) - Python version upgrade syntax
- **N** (pep8-naming) - Naming conventions

**Configuration** (in `pyproject.toml`):
```toml
[tool.ruff]
line-length = 100

[tool.ruff.lint]
select = [
    "C4",  # flake8-comprehensions
    "E",   # pycodestyle
    "ERA", # eradicate (removes commented out code)
    "F",   # pyflakes
    "I",   # isort
    "PLC", # pylint
    "SIM", # flake8-simplify
    "TID", # tidy-imports
    "UP",  # pyupgrade
    "N",   # pep8-naming
    "W",   # warnings
]

[tool.ruff.lint.pydocstyle]
convention = "google"
```

## Consequences

### Positive

- **Extremely fast**: 10-100x faster than existing Python linters (written in Rust)
- **Unified tool**: Replaces multiple tools with a single command
- **Auto-fix capabilities**: Can automatically fix many linting issues
- **Same ecosystem**: From Astral (same team as uv), ensuring excellent integration
- **Pre-commit friendly**: Fast enough to run on every commit

### Negative

- **Relatively new**: Less battle-tested than black/flake8
- **Formatting differences**: Some edge cases may format differently than black

## Alternatives Considered

### black + flake8 + isort

- **Description**: Traditional Python toolchain using multiple specialized tools
- **Why not chosen**: Slower and requires managing multiple configurations

### Rule Suppressions

Avoid `per-file-ignores` in `pyproject.toml` — prefer inline `# noqa` comments so suppressions stay visible next to the code that needs them.

### Line Length in Multiline Strings

For multiline string literals (e.g. prompt templates) that naturally exceed the line-length limit, add `# noqa: E501` on the closing `"""`. Ruff applies this suppression to all lines within the string.

```python
PROMPT = """This is a long prompt template that exceeds
the line-length limit on several lines because it contains
natural-language text that shouldn't be arbitrarily wrapped."""  # noqa: E501
```

---

## Notes

- Install with: `uv add --dev ruff`
- Documentation: https://docs.astral.sh/ruff/
- Common commands:
  - `ruff check .` - Run linter
  - `ruff check --fix .` - Run linter with auto-fix
  - `ruff format .` - Format code
