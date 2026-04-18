# Testing Skills with Executable Code

Any skill that ships executable code — a CLI, script, or programmatic logic the assistant invokes — **must** include tests. This reference covers the test layout and conventions; see `scripting.md` for the underlying skill layout (directory anatomy, shim, base `pyproject.toml`).

## When Tests Are Required

Required when the skill contains:

- A CLI subpackage (`<skill_name>_cli/`) with source code
- A script the assistant or the user invokes directly
- Any programmatic logic that can regress silently when edited

Not required for pure prompt-content skills (SKILL.md + `references/` only). Examples of no-code skills: a workflow-authoring guide, a prompt-template skill, a domain-knowledge reference.

**Why**: When skills evolve, regressions in executable code go unnoticed until they break in production. Tests catch them at authoring time.

## Where Tests Live

Inside the CLI subpackage, next to `src/`:

```
my_skill_cli/
├── pyproject.toml
├── src/
│   └── my_skill_cli/
│       └── ...
└── tests/
    ├── test_cli.py
    └── test_<module>.py
```

See `scripting.md` for the full skill tree around this subpackage.

## pytest Wiring

Add the following to the subpackage's `pyproject.toml` (on top of the base layout described in `scripting.md`):

```toml
[dependency-groups]
dev = ["pytest>=8.0"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- `[dependency-groups] dev` puts pytest in the `dev` group so it isn't installed in production runs.
- `[tool.pytest.ini_options] testpaths = ["tests"]` lets `pytest` run from the subpackage root without arguments.

## Test File Conventions

- **Location**: `<skill_name>_cli/tests/` — sibling of `src/`, not nested inside it
- **Naming**: `test_<module>.py` — one test file per source module under test (e.g., `test_renderer.py` tests `renderer.py`; `test_cli.py` tests `cli.py`)
- **Structure**: Group related tests in `TestCamelCase` classes with `test_snake_case` methods — matches the style used across existing skills
- **Fixtures**: Use `@pytest.fixture` for shared setup; prefer `tmp_path` for filesystem isolation

Example:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from my_skill_cli.renderer import render


@pytest.fixture
def output_path(tmp_path):
    return tmp_path / "out.txt"


class TestRender:
    def test_renders_basic_input(self, output_path):
        render("hello", output_path)
        assert output_path.read_text() == "hello"

    def test_raises_on_empty_input(self):
        with pytest.raises(ValueError):
            render("", Path("/tmp/x"))
```

## Running Tests

From inside the CLI subpackage:

```bash
cd skills/my-skill/my_skill_cli
uv run pytest
```

From anywhere (e.g., from the workspace root or a CI runner):

```bash
uv run --project skills/my-skill/my_skill_cli pytest
```

The `--project` form mirrors the shim invocation (see `scripting.md`) and avoids `cd`. Single tests work the usual way:

```bash
uv run --project skills/my-skill/my_skill_cli pytest tests/test_cli.py::TestName::test_method
```

## What to Cover

Aim for tests that would catch the regressions users would actually hit:

- **Entry points**: The CLI command parses args correctly and dispatches to the right logic
- **Core transformations**: Input → output for the main thing the skill does (rendering, computing, parsing, formatting)
- **Error paths**: Invalid inputs fail predictably with a useful message
- **External dependencies**: Mock or stub network / filesystem calls where practical; favor `tmp_path` fixtures over touching real paths

Avoid over-testing trivial glue or third-party library internals. Tests should fail when the skill's behavior changes, not when an unrelated library version bumps.

## Non-Python Stacks

If a skill uses a different language (see `scripting.md` on when that's appropriate), tests are still required. Use the idiomatic testing tool for the chosen stack (e.g., `vitest`/`jest` for Node, `go test` for Go), and keep the same principles: tests live next to the source, run through a single command, and cover entry points + core logic + error paths.
