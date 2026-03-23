"""Facts memory processor.

Extracts factual information and invariants from conversations that should
persist for future reference.
"""

from tachikoma.agent_defaults import AgentDefaults
from tachikoma.post_processing import PromptDrivenProcessor

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


class FactsProcessor(PromptDrivenProcessor):
    """Post-processor for extracting factual memories.

    Creates or updates topic-named files in memories/facts/.
    """

    def __init__(self, agent_defaults: AgentDefaults) -> None:
        """Initialize the processor.

        Args:
            agent_defaults: Common SDK options (cwd, cli_path, env).
        """
        super().__init__(FACTS_PROMPT, agent_defaults)
