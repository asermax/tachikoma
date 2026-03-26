"""Skills context provider for pre-processing pipeline.

Uses an Opus agent to classify which skills are relevant to the
current user message, then injects the matched skills' content
and agent definitions.
"""

from claude_agent_sdk import ClaudeAgentOptions, query
from claude_agent_sdk.types import AgentDefinition, ResultMessage
from loguru import logger

from tachikoma.agent_defaults import AgentDefaults
from tachikoma.pre_processing import ContextProvider, ContextResult
from tachikoma.skills.registry import SkillRegistry

_log = logger.bind(component="skills_context")

_NO_RELEVANT_SKILLS = "NO_RELEVANT_SKILLS"

SKILL_CLASSIFICATION_PROMPT = """You are a skill classification agent. Your task is to \
determine which skills are relevant to the user's current message.

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


class SkillsContextProvider(ContextProvider):
    """Context provider that detects and loads relevant skills.

    Uses an Opus agent with low effort to classify which skills are
    relevant to the current user message. Returns a ContextResult
    with the "skills" tag containing skill content and their agents.
    """

    def __init__(
        self, agent_defaults: AgentDefaults, *, registry: SkillRegistry
    ) -> None:
        self._agent_defaults = agent_defaults
        self._registry = registry

    async def provide(self, message: str) -> ContextResult | None:
        # R10: No-op when no skills exist in registry
        if not self._registry.skills:
            return None

        skills_list = "\n".join(
            f"- **{name}**: {skill.description}"
            for name, skill in self._registry.skills.items()
        )
        prompt = SKILL_CLASSIFICATION_PROMPT.format(
            skills=skills_list,
            message=message,
        )

        # Defense in depth for tool-less agents (see DES-007 "Disabling Tools"):
        # 1. Default permission mode — headless query() has no can_use_tool callback,
        #    so any tool permission request raises an exception.
        # 2. allowed_tools=[] — documents intent. Currently a no-op due to an SDK bug
        #    (empty list is falsy, so --allowedTools is never passed to CLI).
        # 3. max_turns=3 — hard limit prevents runaway execution.
        options = ClaudeAgentOptions(
            model=self._agent_defaults.model,
            effort="low",
            max_turns=3,
            allowed_tools=[],
            cwd=self._agent_defaults.cwd,
            cli_path=self._agent_defaults.cli_path,
            env=self._agent_defaults.env,
        )

        # Fully consume the query() generator per DES-005 — no early
        # return/break inside the async for loop.
        detected_names: list[str] = []

        try:
            async for sdk_message in query(prompt=prompt, options=options):
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
                                name.strip() for name in result_text.split("\n")
                                if name.strip()
                            ]

                            # R2: discard unrecognized names
                            valid_names = [
                                name for name in raw_names
                                if name in self._registry.skills
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

        skill_blocks: list[str] = []
        filtered_agents: dict[str, AgentDefinition] = {}

        for skill_name in detected_names:
            skill = self._registry.skills[skill_name]

            skill_block = (
                f'<skill name="{skill_name}" directory="{skill.path}">\n'
                f'{skill.body}\n</skill>'
            )
            skill_blocks.append(skill_block)
            filtered_agents.update(self._registry.get_agents_for_skill(skill_name))

        if not skill_blocks:
            return None

        xml_content = "\n\n".join(skill_blocks)

        return ContextResult(
            tag="skills",
            content=xml_content,
            agents=filtered_agents if filtered_agents else None,
        )
