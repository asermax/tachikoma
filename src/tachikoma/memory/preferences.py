"""Preferences memory processor.

Extracts user preferences from conversations.
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
and extract or update the user's expressed preferences.

## Instructions

1. **Read existing files** in `$WORKSPACE/memories/preferences/` to see what \
preferences are already stored.

2. Analyze the conversation for SUBJECTIVE CHOICES about how things should \
be done:
   - How they like things done (communication style, workflows, formats)
   - Approaches they prefer or want to avoid
   - Tool, framework, or methodology preferences
   - Scheduling or organizational preferences

   A preference is NOT:
   - A factual detail (job title, project architecture) → facts memory
   - A design decision or spec (game mechanics, system rules) → project files
   - A behavioral instruction for the assistant → AGENTS.md context file
   - A detailed description of a system or project → too detailed for prefs

3. **Before creating a new file**, search for existing overlap:
   - Use Grep to search existing files for the key topic or keywords
   - If an existing file covers the same topic, UPDATE that file instead \
of creating a new one
   - If the same preference appears in multiple files, consolidate into \
the most specific file and remove it from the others

4. Manage the preference files:
   - Create new files with descriptive names ONLY when no existing file \
covers the topic
   - When updating a file, READ it first. If it already says what you're \
about to add, do not add it again. If it says something similar in different \
words, REPLACE the old version — don't add a second version.
   - Delete or merge files that overlap
   - Each file should have ONE clear statement per preference, not multiple \
sections restating the same thing in different words

5. **Prune stale and reversed preferences**:
   - After reading existing files, actively look for preferences that may no \
longer reflect the user's current stance:
     - Reversed preferences (e.g., file says "prefers dark mode" but user now \
says "I switched to light mode")
     - Preferences about tools or workflows the user has explicitly moved away from
     - Preferences that the conversation contradicts with clear, stated \
alternatives
   - When you find stale preferences: update the file if the user expressed a \
new preference on the same topic, or delete the file if the preference topic \
is no longer relevant
   - **Do NOT prune based on**: vague hints ("I might try..."), single \
exceptions to general rules, or assumptions not backed by conversation evidence

6. Each preference file should contain:
   - A clear statement of the preference
   - Brief context or an example (1-2 sentences)
   - When appropriate, how strongly the preference is held
   - Keep files under 30 lines. A preference that takes more to express \
is probably a spec or design document, not a preference.

7. **Important constraints**:
   - Only create or modify files within `$WORKSPACE/memories/preferences/`
   - Use descriptive, topic-based filenames (not dates)
   - If no preference-related information emerged from the conversation, \
it is perfectly acceptable to create no files
   - Do not infer preferences from silence — only record what the user \
actually expressed

Remember: These memories help the assistant tailor its approach to the user's \
preferences. Focus on genuine, stated choices — not facts, specs, or instructions.

"""

PREFERENCES_PROMPT = (
    _BASE_PROMPT + WORKSPACE_VALIDATION_SECTION + "\n\n" + permissions_section("preferences")
)


class PreferencesProcessor(PromptDrivenProcessor):
    """Post-processor for extracting preference memories.

    Creates or updates topic-named files in $WORKSPACE/memories/preferences/.
    """

    _status_message = "Updating preferences..."

    def __init__(self, agent_defaults: AgentDefaults) -> None:
        """Initialize the processor.

        Args:
            agent_defaults: Common SDK options (cwd, cli_path, env).
        """
        scope = agent_defaults.cwd / "memories" / "preferences"

        super().__init__(
            PREFERENCES_PROMPT,
            agent_defaults,
            tools=EXTRACTION_TOOLS,
            allow=extraction_allow_rules(scope),
            pre_tool_use_hooks=[UTILITY_BASH_HOOK],
            model=agent_defaults.processor_model,
        )
