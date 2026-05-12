"""Shared prompt sections and configuration for memory extraction processors."""

from __future__ import annotations

from pathlib import Path

from tachikoma.post_processing import WORKSPACE_VALIDATION_SECTION, abs_rule

__all__ = ["WORKSPACE_VALIDATION_SECTION"]

CONTEXT_DEDUP_SECTION = """\
## Context File Deduplication

Before creating any new memory file, check whether the information is already \
covered in the foundational context files. These files are maintained as the \
authoritative source for certain categories of information.

1. Read the context files in `$WORKSPACE/context/`:
   - `AGENTS.md` — operational guidelines and workflow preferences
   - `USER.md` — stable identity and high-level project awareness
   - `SOUL.md` — personality and communication style

2. For each piece of information you're about to write:
   - If the SAME information is already in one of these files, do NOT create \
a memory file for it — the context file is the source of truth
   - If a context file partially covers the topic but the conversation adds \
genuinely new details not found there, you MAY create a file — but only for \
the new information, not a restatement of what's already in the context file
   - If no context file covers the topic, proceed normally

This check prevents duplicating information across the memory system and the \
context files, which would create confusion about which is authoritative.

## Skill Deduplication

The session context summary lists any active skills with their directory paths. \
Skill files are the authoritative source for their domain — workflow instructions, \
reference material, and domain conventions belong in skills, not in memory files.

Before creating a memory file, check whether the topic overlaps with an active skill:
1. If the context summary lists active skills, read their SKILL.md (use the \
directory path provided) to understand what the skill covers
2. If the information you're about to record is already fully captured by a \
skill file, do NOT create a memory file for it — the skill is the source of truth
3. If a skill partially covers the topic but the conversation adds genuinely new \
details not in the skill, you MAY create a file — but only for the new information"""


def permissions_section(memory_type: str, *, include_agent: bool = True) -> str:
    """Build the permissions section for a memory extraction prompt."""
    read_note = " (needed for validation)" if include_agent else ""
    agent_note = (
        " You can use the Agent tool to spawn validation sub-agents." if include_agent else ""
    )
    return f"""\
## Permissions

You can read files anywhere in the workspace{read_note}. Edits \
and writes are restricted to `$WORKSPACE/memories/{memory_type}/`. For Bash, read-only \
inspection commands (`ls`, `find`, `file`, `echo`, `date`, `cat`, `head`, \
`tail`, `wc`, `stat`) and navigation (`cd`, `pwd`) are allowed — other \
commands will be denied.{agent_note}"""


EXTRACTION_TOOLS = ["Read", "Glob", "Grep", "Bash", "Edit", "Write", "Agent"]

EPISODIC_TOOLS = ["Read", "Glob", "Grep", "Bash", "Edit", "Write"]


def extraction_allow_rules(scope: Path, *, include_agent: bool = True) -> list[str]:
    """Build the allow rules for a memory extraction processor."""
    rules = [
        "Read",
        "Glob",
        "Grep",
        "Bash",
        abs_rule("Edit", scope),
        abs_rule("Write", scope),
    ]
    if include_agent:
        rules.append("Agent")
    return rules
