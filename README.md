# Tachikoma

A proactive personal assistant built on [Claude Agent SDK](https://github.com/anthropics/claude-code-sdk-python) that remembers, learns, and takes initiative. Named after the think-tanks from Ghost in the Shell — curious, connected, and developing a unique personality through accumulated experience.

## Overview

Unlike traditional AI assistants that are stateless and reactive, Tachikoma maintains persistent memory across conversations, extracts learnings automatically, and processes background tasks during idle time — all accessible through a simple chat interface.

**Key capabilities (planned):**

- **Contextual conversations** — past interactions inform future ones through automatic memory retrieval
- **Memory extraction** — learns facts, preferences, and patterns from conversations without explicit user action
- **Proactive task processing** — queues and executes background tasks during idle time
- **Delegation architecture** — coordinator agent delegates specialized requests to focused sub-agents

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- [just](https://github.com/casey/just) (optional, for task running)

## Setup

```bash
# Clone the repository
git clone https://github.com/asermax/tachikoma.git
cd tachikoma

# Install dependencies
uv sync
```

## Usage

```bash
# Run the agent
just run

# Or directly
PYTHONPATH=src uv run python -m tachikoma
```

## Development

```bash
# Run tests
just test

# Run linting
just lint

# Format code
just fmt

# Type checking
just typecheck

# Run all quality gates (lint + typecheck + test)
just check
```

## Project Structure

```
src/tachikoma/
├── coordinator.py   # Core coordinator agent
├── adapter.py       # Agent adapter layer
├── events.py        # Event system
├── repl.py          # Interactive REPL interface
└── __main__.py      # Entry point
```

## Status

Early development (v0.1.0). See [VISION.md](docs/planning/VISION.md) for the full project vision and [DELTAS.md](docs/planning/DELTAS.md) for the feature inventory.

## License

Private — not yet licensed for distribution.
