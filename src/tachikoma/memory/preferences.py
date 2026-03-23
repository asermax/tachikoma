"""Preferences memory processor.

Extracts user preferences from conversations.
"""

from tachikoma.agent_defaults import AgentDefaults
from tachikoma.post_processing import PromptDrivenProcessor

PREFERENCES_PROMPT = """You are a memory extraction agent. Your task is to analyze
the conversation and extract or update the user's expressed preferences.

## Instructions

1. First, read the existing files in the `memories/preferences/` directory to
   see what preferences are already stored.

2. Analyze the conversation for preference-related statements the user made:
   - How they like things done
   - Their preferred approaches or styles
   - Things they want to avoid
   - Communication preferences
   - Tool or workflow preferences
   - Any subjective choices they've expressed

3. Manage the preference files:
   - Create new files with descriptive names (e.g., `code-style.md`,
     `communication.md`, `workflow.md`)
   - Update existing files when preferences change or are refined
   - Delete files for preferences that are no longer accurate

4. Each preference file should contain:
   - A clear statement of the preference
   - Any relevant context or examples
   - When appropriate, how strongly the preference is held

5. **Important constraints**:
   - Only create or modify files within `memories/preferences/`
   - Use descriptive, topic-based filenames (not dates)
   - If no preference-related information emerged from the conversation,
     it is perfectly acceptable to create no files
   - Do not infer preferences from silence — only record what the user
     actually expressed

Remember: These memories help the assistant tailor its approach to the user's
preferences. Focus on genuine, stated preferences rather than assumptions."""


class PreferencesProcessor(PromptDrivenProcessor):
    """Post-processor for extracting preference memories.

    Creates or updates topic-named files in memories/preferences/.
    """

    def __init__(self, agent_defaults: AgentDefaults) -> None:
        """Initialize the processor.

        Args:
            agent_defaults: Common SDK options (cwd, cli_path, env).
        """
        super().__init__(PREFERENCES_PROMPT, agent_defaults)
