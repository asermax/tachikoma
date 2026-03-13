"""Facts memory processor.

Extracts factual information and invariants from conversations that should
persist for future reference.
"""

from pathlib import Path

from tachikoma.post_processing import PostProcessor, fork_and_consume
from tachikoma.sessions.model import Session

FACTS_PROMPT = """You are a memory extraction agent. Your task is to analyze
the conversation and extract or update factual information that would be useful
to remember for future conversations.

## Instructions

1. First, read the existing files in the `memories/facts/` directory to see
   what facts are already stored.

2. Analyze the conversation for factual information that should persist. This
   can include anything that serves as an invariant or reference for future
   conversations. Examples include:
   - Information about the user (personal details, preferences, context)
   - Important dates or upcoming events
   - Routines or recurring commitments
   - Information about people mentioned in conversation
   - Documentation or notes about libraries/frameworks explored
   - Summaries of pages or resources that were discussed
   - Project-specific knowledge that came up
   - Any other facts worth remembering

3. Manage the fact files:
   - Create new files with descriptive names (e.g., `work-info.md`,
     `important-dates.md`, `react-docs.md`, `team-contacts.md`)
   - Update existing files when new information contradicts or extends
     what's there
   - Delete files that are no longer accurate or relevant

4. Each fact file should contain:
   - Clear, factual statements
   - Relevant context or details
   - When appropriate, the date the information was learned

5. **Important constraints**:
   - Only create or modify files within `memories/facts/`
   - Use descriptive, topic-based filenames (not dates)
   - If no new factual information emerged from the conversation,
     it is perfectly acceptable to create no files
   - Do not infer facts that weren't explicitly stated — only record
     what was actually shared or discussed

Remember: These memories help the assistant maintain context across sessions.
Focus on accurate, verified information that will be useful to recall later."""


class FactsProcessor(PostProcessor):
    """Post-processor for extracting factual memories.

    Creates or updates topic-named files in memories/facts/.
    """

    def __init__(self, cwd: Path) -> None:
        """Initialize the processor.

        Args:
            cwd: The workspace directory for the forked agent.
        """
        self._cwd = cwd

    async def process(self, session: Session) -> None:
        """Extract factual memories from the session.

        Args:
            session: The closed session to process.
        """
        await fork_and_consume(session, FACTS_PROMPT, self._cwd)
