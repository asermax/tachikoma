"""Facts memory processor.

Extracts factual information and invariants from conversations that should
persist for future reference.
"""

from tachikoma.agent_defaults import AgentDefaults
from tachikoma.memory.prompts import (
    CONTEXT_DEDUP_SECTION,
    EXTRACTION_TOOLS,
    STORE_PURPOSE_SECTION,
    WORKSPACE_VALIDATION_SECTION,
    extraction_allow_rules,
    permissions_section,
)
from tachikoma.post_processing import MAIN_PHASE, UTILITY_BASH_HOOK, PromptDrivenProcessor

_BASE_PROMPT = """\
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
   - One-time events: bug fixes, security incidents, feature completions, \
outages, deployment events — these happened once on a specific date and belong \
in episodic memory, not here
   - Full design documents, specs, or game mechanics (project files)
   - Article summaries or reading notes (the reading list skill tracks those)
   - Anything longer than ~40 lines — if it needs that much space, it's \
not a fact, it's a document

3. **Before creating a new file**, search for existing overlap:
   - Use Grep to search existing files for the key topic or keywords
   - If an existing file covers the same topic, UPDATE that file instead \
of creating a new one
   - If information is spread across multiple files about the same topic, \
MERGE them into one file and delete the others

4. **File Consolidation at Write Time**:
   Before creating any new file, follow this sequence — these steps are \
mandatory, not optional search hints:
   - First, list the target directory: `ls $WORKSPACE/memories/facts/`. \
Read the directory contents in full before deciding to write.
   - Identify which existing file (if any) covers the broadest topic that \
encompasses the new information. Match by project name, system name, tool, \
or domain — not by incident, date, or specific event.
   - If a broad-topic file exists, UPDATE that file. Do NOT create an \
incident- or date-specific sibling alongside it (e.g., if `<project>.md` \
exists, do not also create `<project>-<bug-description>-<YYYY-MM-DD>.md`).
   - If multiple existing files cover overlapping aspects of the same topic, \
prefer the most specific existing file that still covers the new information \
broadly — and consider merging the narrower ones into it.
   - Only create a new file when NO existing file covers the topic. When you \
do create one, choose a broad topic name that future related extracts can \
merge into — `<project>.md`, `<system>.md`, `<tool>.md`, `<domain>.md` — \
never a name scoped to one incident, bug, patch, or date.
   - Positive examples (broad, future-mergeable): `<project>.md`, \
`<system>.md`, `<tool>.md`, `<domain>.md`, `work-info.md`, `tech-stack.md`.
   - Negative examples (forbidden — too narrow): \
`<project>-<bug-description>-<YYYY-MM-DD>.md`, \
`<project>-patch-<issue-id>.md`, `<system>-<incident>-<date>.md`, \
`<topic>-session-<date>.md`.

5. Manage the fact files:
   - Create new files with descriptive names ONLY when no existing file \
covers the topic
   - Update existing files when new information extends what's there
   - **Merge** files that overlap in topic — combine into one, delete the rest
   - **Delete** files that are outdated, redundant, or better covered elsewhere
   - When updating a file, READ it first. If a section already covers what \
you're about to add, update that section rather than appending a duplicate

6. **Prune stale and redundant entries**:
   - After reading existing files, actively look for entries that may be \
outdated or no longer accurate based on the conversation:
     - Information contradicted by new statements (e.g., file says "works at \
Company A" but conversation reveals a move to Company B)
     - References to completed projects, past roles, or expired commitments \
that the conversation confirms are done
     - Entries about tools, services, or setups the user no longer uses
   - When you find stale entries: update them if new information replaces the \
old, or delete the file if the entire topic is no longer relevant
   - **Do NOT prune based on**: vague hints ("I might switch..."), old dates \
alone (age is not staleness), or assumptions not backed by conversation evidence

7. Each fact file should contain:
   - Clear, factual statements
   - Relevant context or details
   - Keep files under 40 lines. If a topic needs more detail, the detail \
probably belongs in a project file, not in facts memory.

8. **Important constraints**:
   - Only create or modify files within `$WORKSPACE/memories/facts/`
   - Use descriptive, topic-based filenames (not dates). Good names: \
`work-info.md`, `key-people.md`, `tech-stack.md`. Bad names that indicate \
the content belongs in episodic: `2026-04-15-outage.md`, \
`bug-fix-session.md`, `security-incident-april.md`. Bad names that \
fragment a broad topic into per-incident files: \
`<project>-<bug-description>-<YYYY-MM-DD>.md`, \
`<project>-patch-<issue-id>.md`, `<system>-<incident>-<date>.md` — use \
the broad `<project>.md` or `<system>.md` form instead.
   - If no new factual information emerged from the conversation, \
it is perfectly acceptable to create no files
   - Do not infer facts that weren't explicitly stated — only record \
what was actually shared or discussed
   - Before writing a fact, ask: "Will this still be useful in a month?" \
If no — it describes something that happened once, has a specific date, \
or is a record of an event — it belongs in episodic memory, not here

Remember: These memories help the assistant maintain context across sessions. \
Focus on accurate, stable reference information — not activity logs or documents.

"""

FACTS_PROMPT = (
    _BASE_PROMPT
    + STORE_PURPOSE_SECTION
    + "\n\n"
    + CONTEXT_DEDUP_SECTION
    + "\n\n"
    + WORKSPACE_VALIDATION_SECTION
    + "\n\n"
    + permissions_section("facts")
)


class FactsProcessor(PromptDrivenProcessor):
    """Post-processor for extracting factual memories.

    Creates or updates topic-named files in $WORKSPACE/memories/facts/.
    """

    phase = MAIN_PHASE
    _status_message = "Extracting facts..."

    def __init__(self, agent_defaults: AgentDefaults) -> None:
        """Initialize the processor.

        Args:
            agent_defaults: Common SDK options (cwd, cli_path, env).
        """
        scope = agent_defaults.cwd / "memories" / "facts"

        super().__init__(
            FACTS_PROMPT,
            agent_defaults,
            tools=EXTRACTION_TOOLS,
            allow=extraction_allow_rules(scope),
            pre_tool_use_hooks=[UTILITY_BASH_HOOK],
            model=agent_defaults.processor_model,
        )
