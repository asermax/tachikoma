"""Shared prompt sections and configuration for memory extraction processors."""

from pathlib import Path

from tachikoma.post_processing import abs_rule

WORKSPACE_VALIDATION_SECTION = """\
## Workspace Validation

Before writing a memory that contains claims about workspace state — file paths, \
project structure, configuration values, implementation details — validate each \
claim against the actual workspace:

1. Identify verifiable claims in the memory you're about to write:
   - References to specific files or directories (do they exist? contain what's claimed?)
   - Configuration values (does the config file actually say that?)
   - Implementation details (does the code actually work that way?)
   - Project state (is the project actually in that state?)

2. For verifiable claims, use the Agent tool to spawn validation sub-agents:
   - subagent_type: "Explore"
   - model: "haiku"
   - Batch related claims into a single call where possible
   - The agent should read the relevant file(s) and respond with "VALID" or \
"INVALID: reason" for each claim

3. Only include VALID claims in the written memory:
   - If a claim is INVALID, omit it
   - If ALL claims are invalid, do not create the file

Do NOT validate: subjective information, preferences, general knowledge, \
conversation summaries, personal details — only verifiable claims about \
workspace state."""


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
