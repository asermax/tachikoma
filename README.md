# Tachikoma

A proactive personal assistant built on [Claude Agent SDK](https://github.com/anthropics/claude-code-sdk-python) that remembers, learns, and takes initiative. Named after the think-tanks from Ghost in the Shell — curious, connected, and developing a unique personality through accumulated experience.

## Overview

Unlike traditional AI assistants that are stateless and reactive, Tachikoma maintains persistent memory across conversations, extracts learnings automatically, and processes background tasks during idle time — all accessible through a simple chat interface.

**Key capabilities:**

- **Contextual conversations** — past interactions inform future ones through automatic memory retrieval
- **Memory extraction** — learns facts, preferences, and patterns from conversations without explicit user action
- **Proactive task processing** — queues and executes background tasks during idle time, delivering results as notifications
- **Skills with hot-reload** — domain-specific skill packages detected by LLM classification, with filesystem watching for instant availability
- **Workflow engine** — directory-based workflow definitions with step tracking, MCP tools for lifecycle management, and database persistence
- **Telegram media support** — images, audio, voice, documents, stickers, video, and animations received and processed from Telegram

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)

## Installation

```bash
uv tool install tachikoma-agent
```

### Prerequisites

- [uv](https://docs.astral.sh/uv/getting-started/installation/) installed
- Python 3.12 or later
- An [Anthropic API key](https://console.anthropic.com/)

### First Run

```bash
# Set your API key
export ANTHROPIC_API_KEY="your-key-here"

# Run the agent (starts with REPL channel by default)
tachikoma

# Or explicitly use the run subcommand
tachikoma run

# Use Telegram channel instead
tachikoma run --channel telegram
```

On first run, Tachikoma auto-generates its configuration file at `~/.config/tachikoma/config.toml`.

### Upgrading

```bash
uv tool upgrade tachikoma-agent
```

## Development

[just](https://github.com/casey/just) is used as a task runner for common development commands.

```bash
# Clone the repository
git clone https://github.com/asermax/tachikoma.git
cd tachikoma

# Install dependencies
just install

# Run the agent
just run

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

## Status

Active development (v1.13.1). See [VISION.md](docs/planning/VISION.md) for the full project vision and [DELTAS.md](docs/planning/DELTAS.md) for the feature inventory.

## License

[MIT](LICENSE)
