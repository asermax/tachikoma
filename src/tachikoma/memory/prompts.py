"""Shared prompt sections and configuration for memory extraction processors."""

from __future__ import annotations

from pathlib import Path

from tachikoma.post_processing import WORKSPACE_VALIDATION_SECTION, abs_rule

__all__ = ["WORKSPACE_VALIDATION_SECTION"]


def permissions_section(memory_type: str, *, include_agent: bool = True) -> str:
    """Build the permissions section for a memory extraction prompt."""
    read_note = " (needed for validation)" if include_agent else ""
    agent_note = (
        " You can use the Agent tool to spawn validation sub-agents."
        if include_agent
        else ""
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
