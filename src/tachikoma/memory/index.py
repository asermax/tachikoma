"""Memory index rebuild logic for heavy index maintenance.

Provides prompt constants and helper functions for rebuilding MEMORY.md
index files in facts and preferences directories. Used by both the
bootstrap hook (first-run index creation) and Sunday maintenance
(full rebuild with description workers).
"""

from __future__ import annotations

from tachikoma.agent_defaults import AgentDefaults
from tachikoma.memory.prompts import extraction_allow_rules
from tachikoma.post_processing import UTILITY_BASH_HOOK, query_and_consume

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
MEMORY.md index file in $WORKSPACE/memories/<type>/ from scratch using \
description workers.

## Instructions

1. **List all files**: Use Glob to find all `.md` files in \
`$WORKSPACE/memories/<type>/` (exclude `MEMORY.md` itself).

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

You can read and write files within `$WORKSPACE/memories/<type>/`. You can \
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
    prompt = HEAVY_INDEX_REBUILD_PROMPT
    prompt = prompt.replace("$WORKSPACE", str(agent_defaults.cwd))
    prompt = prompt.replace("<type>", memory_type)

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
