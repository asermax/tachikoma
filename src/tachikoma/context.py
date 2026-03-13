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

# Hard-coded preamble explaining the context system
CONTEXT_PREAMBLE = """\
The following sections contain your foundational context — personality traits, user knowledge,
and behavioral instructions. These files live in the workspace's `context/` directory and are
user-editable. You can suggest updates when you learn something that should be persisted.

Each section is wrapped in XML tags to clearly delineate its purpose."""


def load_context(workspace_path: Path) -> str | None:
    """Read context files and assemble into a system prompt string.

    Synchronous — files are small. Returns None when no content found.

    Args:
        workspace_path: Path to the workspace root directory.

    Returns:
        Assembled system prompt string with preamble and XML-wrapped sections,
        or None if all files are missing or empty.
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
        return None

    return CONTEXT_PREAMBLE + "\n\n" + "\n\n".join(sections)


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

    # Load context and store in extras
    system_prompt = load_context(workspace_path)
    if system_prompt is not None:
        ctx.extras["system_prompt"] = system_prompt
