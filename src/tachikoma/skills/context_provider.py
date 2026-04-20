"""Skills context provider for per-message pre-processing pipeline.

Uses an Opus agent to classify which skills are relevant to the
current user message, then injects the matched skills' content.
Runs on every message, classifying only against skills not already
in context (identified via entry metadata).
"""

from typing import TYPE_CHECKING

from claude_agent_sdk import ClaudeAgentOptions
from claude_agent_sdk.types import ResultMessage
from loguru import logger

from tachikoma.agent_defaults import AgentDefaults
from tachikoma.per_message_pre_processing import (
    MessageContextProvider,
    render_conversation_context,
)
from tachikoma.pre_processing import ContextResult
from tachikoma.sdk_query import stderr_aware_query
from tachikoma.sessions.model import SessionContextEntry

if TYPE_CHECKING:
    from claude_agent_sdk.types import AgentDefinition

from tachikoma.skills.registry import Skill, SkillRegistry, render_skill_block

_log = logger.bind(component="skills_context")

_NO_RELEVANT_SKILLS = "NO_RELEVANT_SKILLS"

_AGENTS_OWNER = "agents"
_NO_AGENTS_CONTEXT = "(No agent instructions available.)"

SKILL_CLASSIFICATION_PROMPT = """You are a skill classification agent. Your task is to \
determine which skills are relevant to the user's current message.

## Agent Instructions

The main agent operates under the following operational conventions. Use them to \
inform relevance judgments — e.g., workflow preferences and domain-specific rules \
that make some skills obviously more applicable than others.

{agents_context}
{conversation_context_section}
## Available Skills

{skills}

## Instructions

1. Analyze the user's message to understand what they are asking or discussing.

2. Compare the message against each skill's name and description to determine relevance.

3. A skill is relevant if:
   - The user's message directly relates to the skill's purpose
   - The skill could help the user accomplish their goal
   - The skill provides context or capabilities that would be useful

4. Return ONLY the names of relevant skills, one per line.

5. If no skills are relevant to the message, respond with exactly: `NO_RELEVANT_SKILLS`

## User's Message

{message}

---

Return the relevant skill names (one per line), or NO_RELEVANT_SKILLS if none apply.
"""


def render_agents_context(entries: list[SessionContextEntry]) -> str:
    """Render the session's `agents` entries as XML blocks for the classifier prompt.

    Filters to entries whose owner is `agents` (from AGENTS.md), preserving ID order,
    and wraps each in `<agents>...</agents>`. Returns a neutral placeholder when no
    `agents` entries exist so the prompt's structure stays stable.
    """
    agents_entries = sorted(
        (e for e in entries if e.owner == _AGENTS_OWNER),
        key=lambda e: e.id,
    )

    if not agents_entries:
        return _NO_AGENTS_CONTEXT

    return "\n\n".join(
        f"<{_AGENTS_OWNER}>\n{e.content}\n</{_AGENTS_OWNER}>" for e in agents_entries
    )


class SkillsContextProvider(MessageContextProvider):
    """Context provider that detects and loads relevant skills per-message.

    Uses an Opus agent with low effort to classify which skills are
    relevant to the current user message. Runs on every message, classifying
    only against skills not already in context (identified via entry metadata).

    Returns one ContextResult per detected skill, each with metadata identifying
    the skill name. Agents are not included — they are derived from entries by
    the coordinator.
    """

    def __init__(self, agent_defaults: AgentDefaults, registry: SkillRegistry) -> None:
        self._agent_defaults = agent_defaults
        self._registry = registry

    def status_message(self, result: list[ContextResult] | None = None) -> str:
        if result is None:
            return "Detecting relevant skills..."

        count = len(result) if result else 0

        if count:
            return f"Loaded {count} skills"

        return "No relevant skills detected"

    async def provide(
        self,
        message: str,
        *,
        existing_entries: list[SessionContextEntry] | None = None,
        sdk_session_id: str | None = None,
        session_summary: str | None = None,
        session_last_exchange: str | None = None,
    ) -> list[ContextResult] | None:
        self._registry.refresh()

        if not self._registry.skills:
            return None

        loaded_names = extract_skill_names(existing_entries or [])
        unloaded_skills = {
            name: skill for name, skill in self._registry.skills.items() if name not in loaded_names
        }

        # Skip classification when no unloaded skills remain
        if not unloaded_skills:
            return None

        skills_list = "\n".join(
            f"- **{name}**: {skill.description}" for name, skill in unloaded_skills.items()
        )
        agents_context = render_agents_context(existing_entries or [])
        conversation_context_section = render_conversation_context(
            session_summary, session_last_exchange
        )
        prompt = SKILL_CLASSIFICATION_PROMPT.format(
            agents_context=agents_context,
            conversation_context_section=conversation_context_section,
            skills=skills_list,
            message=message,
        )

        # Tool-less agent (see DES-007 "Disabling Tools"):
        # 1. tools=[] — sets an empty base tool set (passes --tools "" to CLI).
        # 2. Default permission mode — headless query() has no can_use_tool callback,
        #    so any tool permission request raises an exception.
        # 3. max_turns=10 — hard limit prevents runaway execution.
        options = ClaudeAgentOptions(
            model=self._agent_defaults.searcher_model,
            effort="low",
            max_turns=10,
            tools=[],
            cwd=self._agent_defaults.cwd,
            cli_path=self._agent_defaults.cli_path,
            env=self._agent_defaults.env,
        )

        # Fully consume the query() generator per DES-005 — no early
        # return/break inside the async for loop.
        detected_names: list[str] = []

        try:
            async for sdk_message in stderr_aware_query(prompt=prompt, options=options):
                if isinstance(sdk_message, ResultMessage):
                    if sdk_message.is_error:
                        _log.warning(
                            "Skill classification agent returned error: err={err}",
                            err=sdk_message.result,
                        )
                    elif sdk_message.result is not None:
                        result_text = sdk_message.result.strip()

                        if result_text == _NO_RELEVANT_SKILLS:
                            _log.debug("No relevant skills found for message")
                        else:
                            raw_names = [
                                name.strip() for name in result_text.split("\n") if name.strip()
                            ]

                            valid_names = [
                                name for name in raw_names if name in self._registry.skills
                            ]

                            if not valid_names:
                                _log.warning(
                                    "Classification returned no valid skill names: raw={raw}",
                                    raw=raw_names,
                                )
                            else:
                                detected_names = valid_names
                                _log.debug("Skills detected: names={names}", names=detected_names)

        except Exception as exc:
            _log.exception(
                "Skill classification agent failed: err={err}",
                err=str(exc),
            )

        if not detected_names:
            return None

        ordered_skills: list[Skill] = []
        seen: set[str] = set(loaded_names)

        for name in detected_names:
            try:
                chain = self._registry.resolve_chain(name)
            except Exception as exc:
                _log.exception(
                    "Failed to resolve skill dependency chain: skill={skill}, err={err}",
                    skill=name,
                    err=str(exc),
                )
                continue

            for skill in chain:
                if skill.name in seen:
                    continue
                seen.add(skill.name)
                ordered_skills.append(skill)

        if not ordered_skills:
            return None

        results: list[ContextResult] = []

        for skill in ordered_skills:
            results.append(
                ContextResult(
                    tag=SKILLS_OWNER,
                    content=render_skill_block(skill),
                    metadata={SKILL_NAME_META_KEY: skill.name},
                )
            )

        return results


SKILLS_OWNER = "skills"
SKILL_NAME_META_KEY = "skill_name"


def extract_skill_names(entries: list[SessionContextEntry]) -> set[str]:
    """Extract loaded skill names from context entry metadata.

    Reads metadata["skill_name"] from entries where owner="skills" and metadata
    is not None. Gracefully handles entries without metadata.

    Args:
        entries: List of session context entries to inspect.

    Returns:
        Set of skill names found in entry metadata.
    """
    names: set[str] = set()

    for entry in entries:
        if entry.owner != SKILLS_OWNER or entry.metadata is None:
            continue

        skill_name = entry.metadata.get(SKILL_NAME_META_KEY)
        if skill_name is not None:
            names.add(skill_name)

    return names


def derive_agents_from_entries(
    entries: list[SessionContextEntry], registry: SkillRegistry
) -> dict[str, "AgentDefinition"]:
    """Derive agent definitions from context entries and the skill registry.

    Extracts skill names from entries, then looks up agents for each skill
    from the registry. Silently skips names not in the registry (deleted skills)
    with a debug log.

    Args:
        entries: List of session context entries to extract skill names from.
        registry: The skill registry to look up agent definitions.

    Returns:
        Dictionary mapping namespaced agent names to AgentDefinition instances.
    """
    agents: dict[str, AgentDefinition] = {}
    skill_names = extract_skill_names(entries)

    for name in skill_names:
        if name not in registry.skills:
            _log.debug(
                "Skill not found in registry (may have been deleted): name={name}",
                name=name,
            )
            continue

        skill_agents = registry.get_agents_for_skill(name)
        agents.update(skill_agents)

    return agents
