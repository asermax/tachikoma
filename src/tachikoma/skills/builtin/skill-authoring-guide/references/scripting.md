# Executable Code in Skills

A skill can ship executable code — a CLI or script the assistant invokes — alongside its prompt content. This reference covers the directory layout, naming conventions, shell shim, and `pyproject.toml` wiring used across the existing skills so new skills stay consistent.

Skills with executable code are also **required to ship tests** — see `testing.md` for the test layout and conventions.

## Default Stack

Unless the user explicitly asks for something different, use the standard stack:

- **Python** as the implementation language
- **uv** for dependency and environment management
- **A shell wrapper script** at the skill root as the stable invocation entry point

Everything below assumes this default. Other stacks (Node, Go, pure bash) are acceptable when the user requests them, but authors should still keep the shell wrapper pattern so the assistant's invocation interface stays uniform.

## When to Add Executable Code

Add a CLI subpackage when the skill needs to:

- Perform deterministic work the assistant shouldn't improvise (rendering PDFs, hitting an API, manipulating files with a fixed algorithm)
- Encapsulate logic that would clutter the SKILL.md body or require library dependencies
- Provide a reusable command the user may also invoke directly

Skip the CLI for pure prompt-content skills (guidance, templates, domain knowledge).

## Anatomy of an Executable Skill

The established layout:

```
skills/
└── my-skill/
    ├── SKILL.md                  # Skill metadata + body (hyphenated name)
    ├── my-skill                  # Shell shim (hyphenated, matches folder)
    ├── my_skill_cli/             # Python subpackage (underscored)
    │   ├── pyproject.toml
    │   ├── src/
    │   │   └── my_skill_cli/
    │   │       ├── __init__.py
    │   │       └── cli.py
    │   └── tests/                # See testing.md
    └── references/               # Optional: on-demand docs
```

**Naming**:

- Skill folder and shim: hyphenated (`my-skill`)
- Python subpackage and Python module: underscored (`my_skill_cli`)
- CLI entry name in `[project.scripts]`: hyphenated (`my-skill`), matching the shim name

Why the split: shell tooling (the shim, invocation paths) uses the hyphenated form users type; Python import paths require underscores.

## Shell Shim

Every executable skill has a thin bash shim at its root so the assistant and the user can invoke the CLI without caring about `uv`:

```bash
#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec uv run --project "$SCRIPT_DIR/my_skill_cli" my-skill "$@"
```

Mark it executable (`chmod +x skills/my-skill/my-skill`). The SKILL.md body then documents invocation as:

```bash
./skills/my-skill/my-skill <command> [args...]
```

The `--project` flag makes `uv` resolve the environment from the subpackage's `pyproject.toml` regardless of the caller's cwd. The shim pattern is identical across every skill in the repo — copy it verbatim, change only the `_cli` path and the entry name.

## pyproject.toml (Base)

The base layout for a CLI subpackage:

```toml
[project]
name = "my-skill-cli"
version = "0.1.0"
description = "Short description"
requires-python = ">=3.12"
dependencies = [
    # runtime deps go here
]

[project.scripts]
my-skill = "my_skill_cli.cli:app"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/my_skill_cli"]
```

Key choices:

- **`requires-python = ">=3.12"`** — matches the rest of the ecosystem
- **`[project.scripts]`** — `<hyphenated-name> = "<underscored_module>.cli:app"` — the left side is the command the shim invokes, the right side is the Python entry point
- **Hatchling build backend** — standard across the ecosystem; `[tool.hatch.build.targets.wheel]` points at the `src/` layout

Add testing-specific keys (`[dependency-groups]`, `[tool.pytest.ini_options]`) per `testing.md`.

## CLI Entry Point

The referenced `cli:app` attribute is typically a [`cyclopts`](https://cyclopts.readthedocs.io/) app, matching existing skills (`share-markdown`, `wallet`, `reading-list`, …):

```python
# src/my_skill_cli/cli.py
from cyclopts import App

app = App(name="my-skill", help="Short description of the skill.")


@app.command
def greet(name: str) -> None:
    """Print a greeting."""
    print(f"hello, {name}")
```

Any framework works (argparse, click, typer) as long as `[project.scripts]` points at a callable — cyclopts is the convention in this ecosystem.

## Invoking the CLI

From the assistant's side, always go through the shim:

```bash
./skills/my-skill/my-skill <command> [args...]
```

For debugging or one-off runs without the shim:

```bash
uv run --project skills/my-skill/my_skill_cli my-skill <command>
```

Both forms produce the same result — the shim is just a pre-filled `--project` call.

## Configuration and State

If the skill needs configuration or persistent state, store it under `.tachikoma/config/<skill-name>/` and `.tachikoma/state/<skill-name>/` respectively. Do not read or write inside the skill directory itself — skill directories are authoring artifacts, not writable state.

## Documenting the CLI in SKILL.md

The SKILL.md body should document how to invoke the shim, with examples of the common commands. Keep exhaustive command reference in a separate file (`references/cli.md` is a common choice) and link to it from SKILL.md, following the progressive-disclosure pattern.
