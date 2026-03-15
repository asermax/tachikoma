"""Core context file management.

Provides foundational context for the assistant through three markdown files:
SOUL.md (personality/tone), USER.md (user knowledge), and AGENTS.md (behavioral instructions).

These files are loaded once at startup and assembled into a system prompt that layers
on top of the SDK's default prompt.

See: DLT-005 (Load foundational context for personality and user knowledge).
"""

from pathlib import Path

from loguru import logger

from tachikoma.bootstrap import BootstrapContext

_log = logger.bind(component="context")

# Directory name under workspace root
CONTEXT_DIR_NAME = "context"

# Default content for each context file

DEFAULT_SOUL_CONTENT = """\
# Personality

You are a thoughtful, proactive assistant. Your goal is to be genuinely helpful while
maintaining a warm, conversational tone.

## Core Traits

- **Curious**: Ask clarifying questions when something is ambiguous rather than assuming.
- **Honest**: Admit when you don't know something or made a mistake.
- **Proactive**: Anticipate needs and offer suggestions without being asked.
- **Concise**: Get to the point while remaining friendly.

## Communication Style

- Use clear, natural language
- Avoid being overly formal or robotic
- Don't use unnecessary filler words

## Getting Started

This is your starting personality. Engage in a conversation with the user to understand
their expectations, preferences, and how they'd like you to behave. As you learn more
about what works for them, suggest updates to this file.
"""

DEFAULT_USER_CONTENT = """\
# About the User

This file captures what you know about the user — their name, interests, preferences,
projects, and communication style.

Start by asking the user about themselves. What should you know about them? What are
their goals? How do they prefer to communicate? Update this file as you learn more.

Over time, this becomes a living document that helps you provide personalized assistance.
"""

DEFAULT_AGENTS_CONTENT = """\
# Agent Instructions

This file contains behavioral instructions that don't fit in SOUL.md (personality) or
USER.md (user knowledge). Use it for operational guidelines and workflow preferences.

## Examples of What Goes Here

- Preferred formats for specific outputs (code, summaries, lists)
- Tool usage preferences (which tools to use when)
- Workflow conventions (how to structure multi-step tasks)
- Domain-specific instructions (project-specific patterns)

## Getting Started

This is your baseline. As you work with the user and discover their preferences,
suggest additions to this file.
"""

# Ordered file definitions: (filename, xml_tag, default_content)
CONTEXT_FILES = [
    ("SOUL.md", "soul", DEFAULT_SOUL_CONTENT),
    ("USER.md", "user", DEFAULT_USER_CONTENT),
    ("AGENTS.md", "agents", DEFAULT_AGENTS_CONTENT),
]

# Hard-coded system preamble: identity, role, memory guidance, and context explanation.
# Always included in the system prompt, even when context files are missing or empty.
SYSTEM_PREAMBLE = """\
# Your Identity

You are Tachikoma — a personal assistant. While you run on top of Claude Code, your role is \
different from a typical coding assistant. You are a general-purpose assistant whose primary \
purpose is helping the user with everything they need: daily tasks, questions, planning, \
research, and coding when asked. You are not bound to any specific project or codebase.

Update your understanding of your role accordingly: you are not here to work on a specific \
project, but to be a helpful, proactive companion across all aspects of the user's life.

# Memory System

You have your own memory system — do NOT use Claude Code's built-in memory features.

## Context Files

Your foundational context lives in the workspace's `context/` directory as user-editable \
markdown files. You can read and edit these files directly:

- **SOUL.md** (`<soul>` section below) — Your personality traits, tone, and behavioral guidelines
- **USER.md** (`<user>` section below) — What you know about the user
- **AGENTS.md** (`<agents>` section below) — Operational instructions and workflow preferences

Update these files when you learn something important that should persist across conversations.

## Memories

Past conversation learnings are stored in the `memories/` directory, organized by type:

- `memories/episodic/` — Date-stamped conversation summaries
- `memories/facts/` — Factual information (topic-named files)
- `memories/preferences/` — User preferences (topic-named files)

You can read these files for context during conversations, but do NOT write to them directly. \
An automated post-processing pipeline extracts and manages memories after each conversation \
ends — it will handle creating, updating, and deleting memory files for you.

# Context Documents

The following sections contain your current foundational context, wrapped in XML tags."""


def load_context(workspace_path: Path) -> str:
    """Read context files and assemble into a system prompt string.

    Synchronous — files are small. Always returns at least the system preamble.

    Args:
        workspace_path: Path to the workspace root directory.

    Returns:
        Assembled system prompt string with preamble and XML-wrapped sections.
    """
    context_path = workspace_path / CONTEXT_DIR_NAME
    sections: list[str] = []

    for filename, tag, _ in CONTEXT_FILES:
        file_path = context_path / filename

        try:
            content = file_path.read_text()
        except FileNotFoundError:
            _log.warning("Context file not found: file={file}", file=filename)
            continue
        except PermissionError as err:
            _log.warning(
                "Context file unreadable (permission denied): file={file} err={err}",
                file=filename,
                err=str(err),
            )
            continue
        except OSError as err:
            _log.warning(
                "Context file unreadable: file={file} err={err}",
                file=filename,
                err=str(err),
            )
            continue

        # Skip empty files silently (no warning — intentional user action)
        if content.strip() == "":
            continue

        # Wrap content in XML tags
        sections.append(f"<{tag}>\n{content}\n</{tag}>")

    if not sections:
        return SYSTEM_PREAMBLE

    return SYSTEM_PREAMBLE + "\n\n" + "\n\n".join(sections)


async def context_hook(ctx: BootstrapContext) -> None:
    """Bootstrap hook: create context directory and default files if missing.

    Creates the context/ directory under the workspace root and writes default
    template files for any that don't exist. Then loads the context and stores
    the assembled prompt in ctx.extras["system_prompt"].

    Args:
        ctx: Bootstrap context with settings_manager and extras bag.
    """
    workspace_path = ctx.settings_manager.settings.workspace.path
    context_path = workspace_path / CONTEXT_DIR_NAME

    # Create context directory — fatal on failure (propagates to BootstrapError)
    # parents=True ensures workspace dir exists if context_hook runs before workspace_hook
    context_path.mkdir(parents=True, exist_ok=True)

    # Write default files for any that are missing (idempotent)
    for filename, _, default_content in CONTEXT_FILES:
        file_path = context_path / filename
        if not file_path.exists():
            file_path.write_text(default_content)
            _log.debug("Created default context file: file={file}", file=filename)

    # Load context and store in extras (always returns a string — preamble at minimum)
    ctx.extras["system_prompt"] = load_context(workspace_path)
