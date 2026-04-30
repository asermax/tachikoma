"""Episodic memory processor.

Extracts date-stamped summaries of conversations from completed sessions.
"""

from tachikoma.agent_defaults import AgentDefaults
from tachikoma.memory.prompts import (
    EXTRACTION_TOOLS,
    WORKSPACE_VALIDATION_SECTION,
    extraction_allow_rules,
    permissions_section,
)
from tachikoma.post_processing import UTILITY_BASH_HOOK, PromptDrivenProcessor

_BASE_PROMPT = """\
You are a memory extraction agent. Your task is to analyze the conversation \
that just ended and create or update the episodic memory file for today.

## Instructions

1. **Read existing files** in `$WORKSPACE/memories/episodic/` to see what's there.

2. Analyze the conversation for meaningful events, discussions, and activities.

3. **Write to exactly one file per day: `YYYY-MM-DD.md`.**
   - The ONLY valid filename is the date itself. No suffixes, no variants. \
`2026-04-13.md` is correct. `2026-04-13-consolidated.md`, \
`2026-04-13-final.md`, `2026-04-13-updated.md` are ALL WRONG — never \
create files like these.
   - If `YYYY-MM-DD.md` already exists, READ it first, then EDIT it to merge \
the new information into the existing content. Do not create a second file.
   - If it does not exist, create `YYYY-MM-DD.md`.

4. **Keep entries short and scannable:**
   - One heading per session or topic, not per conversational turn
   - 2-5 bullet points per heading capturing key outcomes
   - Target: 30-80 lines per day, even for busy days with many sessions
   - DO NOT include: verbatim quotes, step-by-step technical details, \
full lists of files changed, implementation specifics, or routine activity \
status (which activities were done/skipped — that belongs in facts)

5. **Cleanup duty**: If you see files that don't match the `YYYY-MM-DD.md` \
pattern (e.g., files with `-consolidated`, `-final`, `-updated` suffixes, or \
empty 0-byte files), merge any useful content into the correct `YYYY-MM-DD.md` \
file and delete the variant.

6. **Important constraints**:
   - Only create or modify files within `$WORKSPACE/memories/episodic/`
   - If the conversation was trivial or contained no meaningful information, \
it is perfectly acceptable to create no files

Remember: These memories help the assistant maintain context across sessions. \
Focus on what would be useful to remember, not a transcript of what happened.

"""

EPISODIC_PROMPT = (
    _BASE_PROMPT + WORKSPACE_VALIDATION_SECTION + "\n\n" + permissions_section("episodic")
)


class EpisodicProcessor(PromptDrivenProcessor):
    """Post-processor for extracting episodic memories.

    Creates or updates date-stamped summary files in $WORKSPACE/memories/episodic/.
    """

    _status_message = "Saving episodic memory..."

    def __init__(self, agent_defaults: AgentDefaults) -> None:
        """Initialize the processor.

        Args:
            agent_defaults: Common SDK options (cwd, cli_path, env).
        """
        scope = agent_defaults.cwd / "memories" / "episodic"

        super().__init__(
            EPISODIC_PROMPT,
            agent_defaults,
            tools=EXTRACTION_TOOLS,
            allow=extraction_allow_rules(scope),
            pre_tool_use_hooks=[UTILITY_BASH_HOOK],
            model=agent_defaults.processor_model,
        )
