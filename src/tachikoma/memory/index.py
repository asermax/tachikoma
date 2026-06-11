"""Memory index rebuild logic and static index injection helpers.

Provides prompt constants and helper functions for rebuilding MEMORY.md
index files in facts and preferences directories. Used by both the
bootstrap hook (first-run index creation) and Sunday maintenance
(full rebuild with description workers).

Also provides ``format_memory_index()`` and ``load_memory_indexes()`` for
static injection of memory indexes into foundational context at startup
and into background task prompts at runtime.
"""

from __future__ import annotations

import re
from pathlib import Path

from loguru import logger

from tachikoma.agent_defaults import AgentDefaults
from tachikoma.memory.prompts import extraction_allow_rules
from tachikoma.post_processing import UTILITY_BASH_HOOK, query_and_consume

_log = logger.bind(component="memory")

# Regex for MEMORY.md entries: [Human-readable Name](./path.md): One-line description
_INDEX_ENTRY_RE = re.compile(
    r"^\[([^\]]+)\]\(\./([^)]+\.md)\):\s*(.+)$", re.MULTILINE
)

# Memory types that have MEMORY.md indexes for static injection.
_INDEXABLE_TYPES = ("facts", "preferences")

# Type descriptions for injected index sections.
_TYPE_DESCRIPTIONS: dict[str, str] = {
    "facts": (
        "Stable reference information: personal details, key people, "
        "technical decisions.\nBrowse the entries below. When a file seems "
        "relevant to the current conversation,\nread it with the Read tool "
        "to get the full content."
    ),
    "preferences": (
        "Subjective choices about how things should be done.\nBrowse the "
        "entries below. When a file seems relevant to the current "
        "conversation,\nread it with the Read tool to get the full content."
    ),
}


def format_memory_index(memory_type: str, raw_content: str) -> str | None:
    """Format a MEMORY.md file's raw content into an injectable section.

    Parses entries matching ``[Name](./path.md): description``.  Malformed
    entries are skipped with a debug log.  Returns ``None`` if no well-formed
    entries are found.

    Args:
        memory_type: Memory type label (``"facts"`` or ``"preferences"``).
        raw_content: Raw text content of a MEMORY.md file.

    Returns:
        Formatted section string, or ``None`` when the file has no usable
        entries.
    """
    # Find all well-formed entries
    entries = _INDEX_ENTRY_RE.findall(raw_content)
    if not entries:
        return None

    # Reconstruct well-formed entry lines from parsed groups
    lines = [f"[{name}](./{path}): {desc}" for name, path, desc in entries]

    # Detect lines that look like entry attempts but don't parse correctly.
    for line in raw_content.splitlines():
        stripped = line.strip()
        if (
            stripped.startswith("[")
            and "](" in stripped
            and not _INDEX_ENTRY_RE.search(stripped)
        ):
            _log.debug(
                "Skipping malformed MEMORY.md entry: type={type} line={line!r}",
                type=memory_type,
                line=stripped,
            )

    description = _TYPE_DESCRIPTIONS.get(
        memory_type,
        "Browse the entries below. When a file seems relevant, read it "
        "with the Read tool.",
    )

    return (
        f"## {memory_type.title()} Index\n\n"
        f"{description}\n\n"
        + "\n".join(f"- {line}" for line in lines)
    )


def load_memory_indexes(workspace_path: Path) -> list[tuple[str, str]]:
    """Read and format both facts and preferences MEMORY.md files.

    Returns a list of ``(owner_tag, formatted_content)`` tuples suitable for
    injection into foundational context.  Skips missing, empty, or malformed
    files silently.

    Args:
        workspace_path: Path to the workspace root directory.

    Returns:
        List of ``("memory_index", formatted_section)`` tuples.
    """
    results: list[tuple[str, str]] = []
    memories_root = workspace_path / "memories"

    for memory_type in _INDEXABLE_TYPES:
        index_path = memories_root / memory_type / "MEMORY.md"

        try:
            raw_content = index_path.read_text()
        except FileNotFoundError:
            continue
        except OSError as err:
            _log.debug(
                "Memory index unreadable: type={type} err={err}",
                type=memory_type,
                err=str(err),
            )
            continue

        formatted = format_memory_index(memory_type, raw_content)
        if formatted is not None:
            results.append(("memory_index", formatted))

    return results

DESCRIPTION_WORKER_PROMPT = """\
You are a memory description worker. You receive a batch of file paths and \
must read each file and produce a one-line description of its contents.

## Instructions

1. Read each file provided in the task using the Read tool.
2. For each file, generate a concise one-line description (under 80 characters) \
that captures the file's topic and scope.
3. Return ALL descriptions in structured XML format.

## Output Format

Return descriptions as XML elements, one per file:

<file path="./filename.md">One-line description of contents</file>
<file path="./other-file.md">Another description</file>

## Rules

- Descriptions must be under 80 characters
- Focus on WHAT the file contains, not its history
- Use plain, descriptive language
- Do not include the filename in the description (it's already in the path attribute)
"""


HEAVY_INDEX_REBUILD_PROMPT = """\
You are a memory index rebuild orchestrator. Your task is to rebuild the \
MEMORY.md index file in $WORKSPACE/memories/{memory_type}/ from scratch using \
description workers.

## Instructions

1. **List all files**: Use Glob to find all `.md` files in \
`$WORKSPACE/memories/{memory_type}/` (exclude `MEMORY.md` itself).

2. **If the directory is empty** (no `.md` files besides MEMORY.md):
   - Write MEMORY.md with only the header: `# Memory Index`
   - Stop — no further steps needed.

3. **Group files into batches**: Divide the file list into batches of 5–8 \
files each. The final batch may be smaller.

4. **Spawn description workers**: For each batch, use the Agent tool to spawn \
a description worker:
   - Set `subagent_type` to `"general-purpose"`
   - Set `model` to `"haiku"`
   - Use DESCRIPTION_WORKER_PROMPT as the system prompt (provide it in the prompt)
   - Pass the batch of file paths in the prompt
   - The worker returns XML: `<file path="./name.md">description</file>`

5. **Collect descriptions**: Gather all `<file>` elements from all workers' \
output. Parse the path and description from each element.

6. **Analyze for structural improvements**:
   - If multiple files have very similar descriptions (same topic/domain), \
consider merging them into a single file with a broad name.
   - If a file's name doesn't match its content (e.g., date-based name for \
general content), rename it to a topic-based name.
   - If files are fragmented (many small files about the same thing), \
consolidate into fewer, broader files.

7. **Perform merges and renames**: If you identified improvements:
   - Read the files to be merged, combine their content into one file
   - Delete the old files
   - Rename files as needed
   - Update your description list to reflect the new file structure

8. **Write MEMORY.md from scratch**: Write a new MEMORY.md with:
   - Header: `# Memory Index`
   - One entry per current file in format: \
`[Human-readable Name](./filename.md): One-line description`
   - Entries are listed in alphabetical order by filename

## Entry Format

Each entry must follow this exact format:
```
[Topic Name](./topic-slug.md): One-line description of what this file contains
```

- The name in brackets is a human-readable topic name (Title Case)
- The path is a relative markdown link starting with `./`
- The description is one line, under 80 characters
- Separate the link and description with `: ` (colon + space)

## Example Output

```
# Memory Index

[API Design](./api-design.md): API architecture decisions and endpoint patterns
[Work Info](./work-info.md): Job details, team structure, and work schedule
```

## Permissions

You can read and write files within `$WORKSPACE/memories/{memory_type}/`. You can \
use the Agent tool to spawn description workers. For Bash, read-only \
inspection commands (`ls`, `find`, `file`, `echo`, `date`, `cat`, `head`, \
`tail`, `wc`, `stat`) and navigation (`cd`, `pwd`) are allowed — other \
commands will be denied.
"""


async def run_index_rebuild(agent_defaults: AgentDefaults, memory_type: str) -> None:
    """Run a heavy index rebuild for a memory directory.

    Spawns a fresh agent (via :func:`query_and_consume`) with the
    heavy rebuild prompt, scoped to the given memory type directory.
    The agent lists files, spawns description workers in batches,
    and writes MEMORY.md from scratch.

    Args:
        agent_defaults: Common SDK options (cwd, cli_path, env).
        memory_type: Memory subdirectory name (e.g. ``"facts"`` or
            ``"preferences"``).
    """
    scope = agent_defaults.cwd / "memories" / memory_type
    prompt = (
        HEAVY_INDEX_REBUILD_PROMPT.replace("$WORKSPACE", str(agent_defaults.cwd))
        .format(memory_type=memory_type)
    )

    # Include the description worker prompt in the main prompt so the
    # orchestrator can pass it to sub-agents.
    prompt = (
        f"{prompt}\n\n<description_worker_prompt>\n"
        f"{DESCRIPTION_WORKER_PROMPT}</description_worker_prompt>"
    )

    await query_and_consume(
        prompt,
        agent_defaults,
        tools=["Read", "Glob", "Grep", "Bash", "Edit", "Write", "Agent"],
        allow=extraction_allow_rules(scope, include_agent=True),
        pre_tool_use_hooks=[UTILITY_BASH_HOOK],
        model=agent_defaults.processor_model,
    )
