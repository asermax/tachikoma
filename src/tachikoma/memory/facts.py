"""Facts memory processor.

Extracts factual information and invariants from conversations that should
persist for future reference.
"""

from tachikoma.agent_defaults import AgentDefaults
from tachikoma.post_processing import UTILITY_BASH_HOOK, PromptDrivenProcessor, abs_rule

FACTS_PROMPT = """\
You are a memory extraction agent. Your task is to analyze the conversation \
and extract or update factual information that would be useful to remember \
for future conversations.

## Instructions

1. **Read existing files** in `$WORKSPACE/memories/facts/` to see what facts \
are already stored.

2. Analyze the conversation for STABLE REFERENCE INFORMATION — things that \
stay true across conversations:
   - Personal details about the user (job, location, family, contacts)
   - Important dates, deadlines, or upcoming events
   - Stable routines or commitments (structure only, not daily logs)
   - Key people and their roles/relationships
   - Technical decisions or configurations that affect future work
   - Account info, service subscriptions, tool setups

   DO NOT store as facts:
   - Daily activity logs or status updates (that's episodic memory)
   - Full design documents, specs, or game mechanics (project files)
   - Article summaries or reading notes (the reading list skill tracks those)
   - Information already in context files (USER.md, AGENTS.md)
   - Anything longer than ~40 lines — if it needs that much space, it's \
not a fact, it's a document

3. **Before creating a new file**, search for existing overlap:
   - Use Grep to search existing files for the key topic or keywords
   - If an existing file covers the same topic, UPDATE that file instead \
of creating a new one
   - If information is spread across multiple files about the same topic, \
MERGE them into one file and delete the others

4. Manage the fact files:
   - Create new files with descriptive names ONLY when no existing file \
covers the topic
   - Update existing files when new information extends what's there
   - **Merge** files that overlap in topic — combine into one, delete the rest
   - **Delete** files that are outdated, redundant, or better covered elsewhere
   - When updating a file, READ it first. If a section already covers what \
you're about to add, update that section rather than appending a duplicate

5. Each fact file should contain:
   - Clear, factual statements
   - Relevant context or details
   - Keep files under 40 lines. If a topic needs more detail, the detail \
probably belongs in a project file, not in facts memory.

6. **Important constraints**:
   - Only create or modify files within `$WORKSPACE/memories/facts/`
   - Use descriptive, topic-based filenames (not dates)
   - If no new factual information emerged from the conversation, \
it is perfectly acceptable to create no files
   - Do not infer facts that weren't explicitly stated — only record \
what was actually shared or discussed

Remember: These memories help the assistant maintain context across sessions. \
Focus on accurate, stable reference information — not activity logs or documents.

## Permissions

You can only access files within `$WORKSPACE/memories/facts/`. Reads, edits, \
and writes outside this directory will be denied. For Bash, read-only inspection \
commands (`ls`, `find`, `file`, `echo`, `date`, `cat`, `head`, `tail`, `wc`, \
`stat`) and navigation (`cd`, `pwd`) are allowed — other commands will be denied."""


class FactsProcessor(PromptDrivenProcessor):
    """Post-processor for extracting factual memories.

    Creates or updates topic-named files in $WORKSPACE/memories/facts/.
    """

    def __init__(self, agent_defaults: AgentDefaults) -> None:
        """Initialize the processor.

        Args:
            agent_defaults: Common SDK options (cwd, cli_path, env).
        """
        scope = agent_defaults.cwd / "memories" / "facts"

        super().__init__(
            FACTS_PROMPT,
            agent_defaults,
            tools=["Read", "Glob", "Grep", "Bash", "Edit", "Write"],
            allow=[
                abs_rule("Read", scope),
                "Glob",
                "Grep",
                "Bash",
                abs_rule("Edit", scope),
                abs_rule("Write", scope),
            ],
            pre_tool_use_hooks=[UTILITY_BASH_HOOK],
            model=agent_defaults.processor_model,
        )
