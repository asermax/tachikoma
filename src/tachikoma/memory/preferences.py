"""Preferences memory processor.

Extracts user preferences from conversations.
"""

from tachikoma.agent_defaults import AgentDefaults
from tachikoma.memory.prompts import (
    CLASSIFICATION_EXAMPLES_SECTION,
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
and extract or update the user's expressed preferences.

## Instructions

1. **Read existing files** in `$WORKSPACE/memories/preferences/` to see what \
preferences are already stored. Also read `$WORKSPACE/context/AGENTS.md` if it \
exists — this file contains operational instructions and workflow preferences. \
If it doesn't exist or is empty, proceed normally — this check is purely to \
avoid duplication.

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
   - An implementation detail or system behavior (how something works \
technically) → facts memory or project docs
   - A bug report, resolved issue, or one-time fix → transient, not a \
lasting preference
   - A procedural workflow step → skill reference files
   - Content already covered in AGENTS.md, SOUL.md, or skill reference \
files → those files already capture it
   - **Financial reference data** (payment structures, rates, fee \
schedules, reconciliation procedures) → facts memory
   - **Technical specifications** (environment variables, SDK/API \
limitations, tool capabilities, system requirements) → facts memory
   - **Procedural workflows** (cleanup steps, diagnostic procedures, \
deployment sequences) → facts memory or skill reference files
   - **System configuration records** (what was configured, where, \
with what values) → facts memory

2b. **Classification self-check**: Before writing any file, ask yourself: \
"Is this describing HOW SOMETHING WORKS (a fact) or HOW THE USER WANTS IT \
DONE (a preference)?"
   - "How it works" → route to facts memory — do NOT create a \
preferences file
   - "How the user wants it done" → valid preference, proceed
   - When uncertain, prefer facts over preferences — objective \
information does not become a preference just because the user mentioned it

3. **Before creating a new file**, search for existing overlap:
   - Use Grep to search existing files for the key topic or keywords
   - Also search `$WORKSPACE/context/AGENTS.md` for the same topic. If \
AGENTS.md already captures the preference (even in different words), skip \
creating a new file — the information is already stored where it belongs
   - If an existing file covers the same topic, UPDATE that file instead \
of creating a new one
   - If the same preference appears in multiple files, consolidate into \
the most specific file and remove it from the others

4. **File Consolidation at Write Time**:
   Before creating any new file, follow this mandatory sequence:
   - First, list the target directory: `ls $WORKSPACE/memories/preferences/`.
   - Identify which existing file (if any) covers the broadest preference \
topic that encompasses the new information. Match by topic area (style, \
workflow, communication, tooling), project, system, or domain — not by a \
specific occasion, date, or one-off interaction.
   - If a broad-topic file exists, UPDATE that file. Do NOT create an \
occasion- or date-specific sibling alongside it (e.g., if \
`<topic-area>-style.md` exists, do not also create \
`<topic-area>-feedback-<YYYY-MM-DD>.md`).
   - If multiple existing files cover overlapping aspects of the same topic, \
prefer the most specific existing file that still covers the new information \
broadly — and consider merging the narrower ones into it.
   - Only create a new file when NO existing file covers the topic. When you \
do create one, choose a broad topic name that future related extracts can \
merge into — `<topic-area>-style.md`, `<topic-area>-workflow.md`, \
`<domain>.md`, `<project>.md` — never a name scoped to one occasion, \
feedback moment, or date.
   - Positive examples (broad, future-mergeable): `<topic-area>-style.md`, \
`<topic-area>-workflow.md`, `<domain>.md`, `<project>.md`, \
`communication-style.md`, `code-formatting.md`.
   - Negative examples (forbidden — too narrow): \
`<topic-area>-feedback-<YYYY-MM-DD>.md`, \
`<project>-preference-<issue-id>.md`, \
`<topic-area>-session-<date>.md`, `<one-off>-incident-<date>.md`.

5. Manage the preference files:
   - Create new files with descriptive names ONLY when no existing file \
covers the topic
   - When updating a file, READ it first. If it already says what you're \
about to add, do not add it again. If it says something similar in different \
words, REPLACE the old version — don't add a second version.
   - Delete or merge files that overlap
   - Each file should have ONE clear statement per preference, not multiple \
sections restating the same thing in different words

6. **Prune stale and reversed preferences**:
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

7. Each preference file should contain:
   - A clear statement of the preference
   - Brief context or an example (1-2 sentences)
   - When appropriate, how strongly the preference is held
   - Keep files under 30 lines. A preference that takes more to express \
is probably a spec or design document, not a preference.

8. **Important constraints**:
   - Only create or modify files within `$WORKSPACE/memories/preferences/`
   - Use descriptive, topic-based filenames (not dates). Good names: \
`communication-style.md`, `code-formatting.md`, `<topic-area>-workflow.md`. \
For per-occasion fragmentation patterns to avoid, see step 4's negative \
examples.
   - If no preference-related information emerged from the conversation, \
it is perfectly acceptable to create no files
   - Do not infer preferences from silence — only record what the user \
actually expressed

Remember: These memories help the assistant tailor its approach to the user's \
preferences. Focus on genuine, stated choices — not facts, specs, or instructions.

"""

PREFERENCES_PROMPT = (
    _BASE_PROMPT
    + CLASSIFICATION_EXAMPLES_SECTION
    + "\n\n"
    + STORE_PURPOSE_SECTION
    + "\n\n"
    + CONTEXT_DEDUP_SECTION
    + "\n\n"
    + WORKSPACE_VALIDATION_SECTION
    + "\n\n"
    + permissions_section("preferences")
)


class PreferencesProcessor(PromptDrivenProcessor):
    """Post-processor for extracting preference memories.

    Creates or updates topic-named files in $WORKSPACE/memories/preferences/.
    """

    phase = MAIN_PHASE
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
