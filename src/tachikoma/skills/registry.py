"""Skill registry for discovering and loading skills and their agents.

The SkillRegistry scans the workspace/skills/ directory at initialization,
loading SKILL.md metadata and agent definitions from each skill's agents/
subdirectory. All discovered agents are made available through get_agents().
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import frontmatter
from claude_agent_sdk.types import AgentDefinition
from loguru import logger

# Valid model values for AgentDefinition
ModelType = Literal["sonnet", "opus", "haiku", "inherit"] | None

_log = logger.bind(component="skills")


@dataclass(frozen=True)
class Skill:
    """Metadata for a discovered skill.

    Attributes:
        name: Skill name (matches folder name).
        description: Human-readable description.
        version: Optional version string.
    """

    name: str
    description: str
    version: str | None = None


class SkillRegistry:
    """Discovers and loads skills and their agents at startup.

    Skills are directory-based packages in workspace/skills/ containing:
    - SKILL.md: Metadata file with YAML frontmatter (name, description, version)
    - agents/: Optional subdirectory with agent definition files (.md)

    Agent definitions are markdown files with YAML frontmatter containing:
    - description: Required string describing the agent
    - model: Optional model name (sonnet, opus, haiku, inherit)
    - tools: Optional list of tool names

    Agents are namespaced as "skill-name/agent-name" to prevent collisions.

    Error handling is graceful: invalid skills/agents are logged as warnings
    and skipped, allowing the system to continue with valid entries.
    """

    def __init__(self, workspace_path: Path) -> None:
        self._agents: dict[str, AgentDefinition] = {}
        self._skills: dict[str, Skill] = {}

        skills_path = workspace_path / "skills"
        self._discover(skills_path)

    def get_agents(self) -> dict[str, AgentDefinition]:
        """Return all discovered agents indexed by namespace.

        Returns:
            Dictionary mapping "skill-name/agent-name" to AgentDefinition.
        """
        return self._agents

    @property
    def skills(self) -> dict[str, Skill]:
        """Return all discovered skill metadata.

        Returns:
            Dictionary mapping skill name to Skill metadata.
        """
        return self._skills

    def _discover(self, skills_path: Path) -> None:
        """Scan skills directory and load all valid skills and agents."""
        if not skills_path.exists():
            _log.debug("Skills directory does not exist: path={path}", path=str(skills_path))
            return

        try:
            items = list(skills_path.iterdir())
        except Exception as exc:
            _log.warning(
                "Failed to list skills directory: path={path}, err={err}",
                path=str(skills_path),
                err=str(exc),
            )
            return

        for item in items:
            # Only process directories
            if not item.is_dir():
                continue

            try:
                self._load_skill(item)
            except Exception as exc:
                _log.warning(
                    "Failed to load skill: skill={skill}, err={err}",
                    skill=item.name,
                    err=str(exc),
                )

    def _load_skill(self, skill_dir: Path) -> None:
        """Load a single skill and its agents.

        Args:
            skill_dir: Path to the skill directory.
        """
        skill_file = skill_dir / "SKILL.md"

        if not skill_file.exists():
            _log.warning(
                "Skill missing SKILL.md: skill={skill}, path={path}",
                skill=skill_dir.name,
                path=str(skill_file),
            )
            return

        try:
            post = frontmatter.load(str(skill_file))
        except Exception as exc:
            _log.warning(
                "Failed to parse SKILL.md: skill={skill}, path={path}, err={err}",
                skill=skill_dir.name,
                path=str(skill_file),
                err=str(exc),
            )
            return

        # Extract and validate skill metadata
        name = post.metadata.get("name", "")
        description = post.metadata.get("description", "")
        version = post.metadata.get("version")

        if not name or not isinstance(name, str):
            _log.warning(
                "Skill has missing or invalid name: skill={skill}",
                skill=skill_dir.name,
            )
            return

        if not description or not isinstance(description, str):
            _log.warning(
                "Skill has missing or invalid description: skill={skill}",
                skill=skill_dir.name,
            )
            return

        if name != skill_dir.name:
            _log.warning(
                "Skill name mismatch: folder={folder}, frontmatter={frontmatter}",
                folder=skill_dir.name,
                frontmatter=name,
            )
            return

        # Store skill metadata (version from YAML is object, need to cast)
        version_str: str | None = version if isinstance(version, str) else None

        skill = Skill(name=name, description=description, version=version_str)
        self._skills[name] = skill

        _log.debug(
            "Loaded skill: name={name}, description={desc}",
            name=name,
            desc=description[:50] + "..." if len(description) > 50 else description,
        )

        # Load agents if agents/ directory exists
        agents_dir = skill_dir / "agents"
        if agents_dir.exists() and agents_dir.is_dir():
            self._load_agents(agents_dir, name)

    def _load_agents(self, agents_dir: Path, skill_name: str) -> None:
        """Load all agents from a skill's agents/ directory.

        Args:
            agents_dir: Path to the agents/ directory.
            skill_name: Name of the parent skill (for namespacing).
        """
        try:
            items = list(agents_dir.iterdir())
        except Exception as exc:
            _log.warning(
                "Failed to list agents directory: skill={skill}, path={path}, err={err}",
                skill=skill_name,
                path=str(agents_dir),
                err=str(exc),
            )
            return

        for item in items:
            # Only process .md files
            if not item.is_file() or item.suffix != ".md":
                continue

            try:
                self._load_agent(item, skill_name)
            except Exception as exc:
                _log.warning(
                    "Failed to load agent: skill={skill}, agent={agent}, err={err}",
                    skill=skill_name,
                    agent=item.stem,
                    err=str(exc),
                )

    def _load_agent(self, agent_path: Path, skill_name: str) -> None:
        """Load a single agent definition.

        Args:
            agent_path: Path to the agent markdown file.
            skill_name: Name of the parent skill (for namespacing).
        """
        agent_name = agent_path.stem
        namespace = f"{skill_name}/{agent_name}"

        try:
            post = frontmatter.load(str(agent_path))
        except Exception as exc:
            _log.warning(
                "Failed to parse agent file: skill={skill}, agent={agent}, path={path}, err={err}",
                skill=skill_name,
                agent=agent_name,
                path=str(agent_path),
                err=str(exc),
            )
            return

        # Extract and validate agent metadata
        description = post.metadata.get("description", "")
        model = post.metadata.get("model")
        tools = post.metadata.get("tools")

        if not description or not isinstance(description, str):
            _log.warning(
                "Agent has missing or invalid description: skill={skill}, agent={agent}",
                skill=skill_name,
                agent=agent_name,
            )
            return

        # Model is passed through without validation (SDK validates at delegation time)
        if model is not None and not isinstance(model, str):
            _log.warning(
                "Agent has invalid model type (expected string): skill={skill}, agent={agent}",
                skill=skill_name,
                agent=agent_name,
            )
            return

        # Tools should be a list of strings if provided
        if tools is not None and (
            not isinstance(tools, list) or not all(isinstance(t, str) for t in tools)
        ):
            _log.warning(
                "Agent has invalid tools format (expected list of strings): "
                "skill={skill}, agent={agent}",
                skill=skill_name,
                agent=agent_name,
            )
            return

        # Prompt is the markdown body (can be empty)
        prompt = post.content

        # Create AgentDefinition
        # Note: model and tools are passed as-is; SDK validates at delegation time
        # We pass None for model if not a valid literal to satisfy type checker
        valid_model: ModelType = None
        if model == "sonnet":
            valid_model = "sonnet"
        elif model == "opus":
            valid_model = "opus"
        elif model == "haiku":
            valid_model = "haiku"
        elif model == "inherit":
            valid_model = "inherit"

        valid_tools: list[str] | None = None
        if tools is not None and isinstance(tools, list):
            valid_tools = [str(t) for t in tools if isinstance(t, str)]

        agent_def = AgentDefinition(
            description=description,
            prompt=prompt,
            model=valid_model,
            tools=valid_tools,
        )

        self._agents[namespace] = agent_def

        _log.debug(
            "Loaded agent: namespace={ns}, description={desc}",
            ns=namespace,
            desc=description[:50] + "..." if len(description) > 50 else description,
        )
