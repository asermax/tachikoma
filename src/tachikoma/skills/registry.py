"""Skill registry for discovering and loading skills and their agents.

The SkillRegistry scans multiple skill source directories at initialization,
loading SKILL.md metadata and agent definitions from each skill's agents/
subdirectory. All discovered agents are made available through get_agents().

Sources are scanned in order with last-wins precedence: if a skill name appears
in multiple sources, the later source completely replaces the earlier one
(metadata, body, and agents). When marked dirty by a filesystem watcher, the
registry re-scans all sources on the next refresh, using swap-on-success to
preserve the previous state on failure.
"""

import contextlib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import frontmatter
from claude_agent_sdk.types import AgentDefinition
from loguru import logger

from tachikoma.workflows.composition import detect_cycles, validate_references
from tachikoma.workflows.definition import WorkflowDefinition
from tachikoma.workflows.loader import load_workflows

# Valid model values for AgentDefinition
ModelType = Literal["sonnet", "opus", "haiku", "inherit"] | None

_log = logger.bind(component="skills")


@dataclass(frozen=True)
class Skill:
    """Metadata for a discovered skill.

    Attributes:
        name: Skill name (matches folder name).
        description: Human-readable description.
        body: Markdown body content (frontmatter stripped).
        path: Path to the skill directory.
        version: Optional version string.
        depends_on: Tuple of declared direct dependency skill names.
        namespace: Plugin alias for namespaced skills, None for default namespace.
    """

    name: str
    description: str
    body: str
    path: Path
    version: str | None = None
    depends_on: tuple[str, ...] = ()
    namespace: str | None = None

    @property
    def qualified_name(self) -> str:
        """Return the fully qualified skill name.

        For default-namespace skills (namespace=None), returns the bare name.
        For plugin skills, returns '<alias>:<name>'.
        """
        if self.namespace is not None:
            return f"{self.namespace}:{self.name}"
        return self.name


def render_skill_block(skill: Skill) -> str:
    """Format a skill as an XML block for prompt injection."""
    return f'<skill name="{skill.qualified_name}" directory="{skill.path}">\n{skill.body}\n</skill>'


class SkillRegistry:
    """Discovers and loads skills and their agents at startup.

    Skills are directory-based packages in any of the source directories containing:
    - SKILL.md: Metadata file with YAML frontmatter (description, version)
    - agents/: Optional subdirectory with agent definition files (.md)

    Agent definitions are markdown files with YAML frontmatter containing:
    - description: Required string describing the agent
    - model: Optional model name (sonnet, opus, haiku, inherit)
    - tools: Optional list of tool names

    Agents are namespaced as "skill-name/agent-name" to prevent collisions.

    Error handling is graceful: invalid skills/agents are logged as warnings
    and skipped, allowing the system to continue with valid entries.

    Multiple sources are scanned in order with last-wins precedence: if a skill
    name appears in multiple sources, the later source completely replaces the
    earlier one (all metadata, body, and agents). When marked dirty by a
    filesystem watcher, the registry re-scans all sources on the next refresh.
    """

    def __init__(self, skill_sources: list[Path]) -> None:
        self._agents: dict[str, AgentDefinition] = {}
        self._skills: dict[str, Skill] = {}
        self._workflows: dict[tuple[str, str], WorkflowDefinition] = {}
        self._chain_cache: dict[str, list[Skill]] = {}
        self._dirty: bool = False
        self._skill_sources = skill_sources
        self._namespaced_source_paths: dict[str, list[Path]] = {}

        for source in skill_sources:
            self._discover(source)

        self._validate_deps()

    def get_agents(self) -> dict[str, AgentDefinition]:
        """Return all discovered agents indexed by namespace.

        Returns:
            Dictionary mapping "skill-name/agent-name" to AgentDefinition.
        """
        return self._agents

    def get_agents_for_skill(self, skill_name: str) -> dict[str, AgentDefinition]:
        """Return agents belonging to a specific skill.

        Args:
            skill_name: The skill name to filter by.

        Returns:
            Dictionary mapping "skill-name/agent-name" to AgentDefinition
            for agents belonging to the given skill.
        """
        prefix = f"{skill_name}/"

        return {ns: agent for ns, agent in self._agents.items() if ns.startswith(prefix)}

    @property
    def skills(self) -> dict[str, Skill]:
        """Return all discovered skill metadata.

        Returns:
            Dictionary mapping skill name to Skill metadata.
        """
        return self._skills

    @property
    def workflows(self) -> dict[tuple[str, str], WorkflowDefinition]:
        """Return all discovered workflow definitions.

        Returns:
            Dictionary mapping (skill_name, workflow_name) tuple to WorkflowDefinition.
        """
        return self._workflows

    def get_workflow(self, skill_name: str, workflow_name: str) -> WorkflowDefinition | None:
        """Return a specific workflow definition.

        Args:
            skill_name: The skill name.
            workflow_name: The workflow name.

        Returns:
            WorkflowDefinition if found, None otherwise.
        """
        return self._workflows.get((skill_name, workflow_name))

    def resolve_chain(self, skill_name: str) -> list[Skill]:
        """Return the transitive dependency chain for a skill, deps-first with anchor last.

        Cycles (including self-reference) are broken via a visited-set; unknown dep
        names are silently skipped. Results are memoized until the next refresh() or
        add_source() call.

        Dep resolution rules (per S13):
        - bare 'dep' → resolve in default namespace (self._skills.get(dep))
        - ':dep' (leading colon) → resolve in the current skill's own plugin namespace
        - '<other>:dep' → resolve in the named plugin namespace

        Raises:
            KeyError: If skill_name is not in self._skills.
        """
        if skill_name in self._chain_cache:
            return self._chain_cache[skill_name]

        if skill_name not in self._skills:
            raise KeyError(skill_name)

        visited: set[str] = set()
        chain: list[Skill] = []

        def dfs(name: str) -> None:
            if name in visited:
                return
            if name not in self._skills:
                return
            visited.add(name)
            current = self._skills[name]
            for dep in current.depends_on:
                resolved = self._resolve_dep_for_validation(dep, current)
                if resolved is not None:
                    dfs(resolved)
            chain.append(current)

        dfs(skill_name)
        self._chain_cache[skill_name] = chain
        return chain

    def mark_dirty(self) -> None:
        """Mark the registry as needing refresh.

        Called by the filesystem watcher when skill files change.
        The next refresh() call will re-discover skills from disk.
        """
        self._dirty = True

    def add_source(self, path: Path) -> None:
        """Add a skill source and discover its skills immediately.

        Must be called during startup before message processing begins.
        Added sources are included in subsequent refresh() scans.
        """
        self._chain_cache.clear()
        self._skill_sources.append(path)
        self._discover(path)
        self._validate_deps()

    def add_namespaced_source(self, alias: str, path: Path) -> None:
        """Add a namespaced skill source from a plugin.

        Skills discovered under this path are registered with namespace=alias,
        keyed in _skills as '<alias>:<name>'. The path is tracked in
        _namespaced_source_paths (NOT _skill_sources) for refresh symmetry.

        Per-skill errors are isolated: a bad skill in one plugin does not
        prevent other skills from the same plugin from loading (R9).

        Args:
            alias: Plugin alias used as the namespace prefix.
            path: Path to the skill source directory.
        """
        self._namespaced_source_paths.setdefault(alias, []).append(path)

        if not path.exists():
            _log.debug(
                "Namespaced skills directory does not exist: alias={alias}, path={path}",
                alias=alias,
                path=str(path),
            )
            self._chain_cache.clear()
            self._validate_deps()
            return

        try:
            items = list(path.iterdir())
        except Exception as exc:
            _log.warning(
                "Failed to list namespaced skills directory: alias={alias}, path={path}, err={err}",
                alias=alias,
                path=str(path),
                err=str(exc),
            )
            self._chain_cache.clear()
            self._validate_deps()
            return

        for item in items:
            if not item.is_dir():
                continue
            try:
                self._load_skill(item, namespace=alias)
            except Exception as exc:
                _log.warning(
                    "Failed to load namespaced skill: alias={alias}, skill={skill}, err={err}",
                    alias=alias,
                    skill=item.name,
                    err=str(exc),
                )

        self._chain_cache.clear()
        self._validate_deps()

    def remove_namespaced_source(self, alias: str) -> None:
        """Remove all skills and agents registered under a plugin namespace.

        Drops every key in _skills where namespace == alias, every key in
        _agents matching prefix '<alias>:', and every key in _workflows whose
        first element starts with '<alias>:'. Clears the chain cache.

        Args:
            alias: Plugin alias to remove.
        """
        prefix = f"{alias}:"

        self._skills = {k: v for k, v in self._skills.items() if v.namespace != alias}
        self._agents = {k: v for k, v in self._agents.items() if not k.startswith(prefix)}
        self._workflows = {k: v for k, v in self._workflows.items() if not k[0].startswith(prefix)}

        self._namespaced_source_paths.pop(alias, None)
        self._chain_cache.clear()

    def _resolve_dep_for_validation(self, dep: str, skill: Skill) -> str | None:
        """Resolve a dep string for validation purposes (returns qualified name or None)."""
        if dep.startswith(":"):
            if skill.namespace is None:
                return None
            return f"{skill.namespace}:{dep[1:]}"
        elif ":" in dep:
            return dep
        else:
            return dep

    def _validate_deps(self) -> None:
        """Validate composition graph and skill dependencies.

        Runs composition validation (cycle detection + reference validation)
        before the existing depends_on / required_skills checks.  Rejected
        workflows are removed from ``self._workflows``.
        """
        # Composition graph validation

        # Mutex pre-pass: a step cannot declare both `composes` and `loop`.
        mutex_violations: list[tuple[str, str]] = []
        for vertex, wf_def in list(self._workflows.items()):
            for step in wf_def.steps:
                if step.composes and step.loop:
                    _log.warning(
                        "Workflow rejected: step declares both `composes` and `loop`: "
                        "skill={skill}, workflow={workflow}, step={step}",
                        skill=vertex[0],
                        workflow=vertex[1],
                        step=step.id,
                    )
                    mutex_violations.append(vertex)
                    break
        for v in mutex_violations:
            self._workflows.pop(v, None)

        sccs = detect_cycles(self._workflows)
        cycles_flat: set[tuple[str, str]] = set()
        for scc in sccs:
            cycles_flat.update(scc)
            _log.warning(
                "Composition cycle detected, rejecting: cycle={members}",
                members=scc,
            )

        bad_refs = validate_references(self._workflows, already_rejected=cycles_flat)
        rejected = cycles_flat | bad_refs

        for key in rejected:
            self._workflows.pop(key, None)

        # Existing depends_on / required_skills validation
        for name, skill in self._skills.items():
            if not skill.depends_on:
                continue
            missing = []
            for dep in skill.depends_on:
                resolved = self._resolve_dep_for_validation(dep, skill)
                if resolved is None or resolved not in self._skills:
                    missing.append(dep)
            if missing:
                _log.warning(
                    "Skill declares unknown dependencies: skill={skill}, missing={missing}",
                    skill=name,
                    missing=missing,
                )

        for workflow_def in self._workflows.values():
            for step in workflow_def.steps:
                if not step.required_skills:
                    continue
                missing_required = [s for s in step.required_skills if s not in self._skills]
                if missing_required:
                    _log.warning(
                        "Workflow step declares unknown required_skills: "
                        "skill={skill}, workflow={workflow}, step={step}, missing={missing}",
                        skill=workflow_def.skill_name,
                        workflow=workflow_def.workflow_name,
                        step=step.id,
                        missing=missing_required,
                    )

    def refresh(self) -> None:
        """Re-scan skills directory if dirty, using swap-on-success.

        If the registry is not dirty, this is a no-op.
        If dirty, saves old dict references, builds fresh dicts via _discover(),
        and swaps them on success. On failure, restores old references and leaves
        _dirty=True so the next refresh() will retry.

        Namespaced sources are re-scanned alongside default sources so that
        plugin skills retain their <alias>:<name> keys across refresh cycles.
        """
        if not self._dirty:
            return

        # Save old references for potential restore
        old_agents = self._agents
        old_skills = self._skills
        old_workflows = self._workflows

        # Assign fresh dicts for _discover() to populate
        self._agents = {}
        self._skills = {}
        self._workflows = {}
        self._chain_cache = {}

        try:
            for source in self._skill_sources:
                self._discover(source)

            # Re-scan namespaced sources to preserve plugin skills
            for alias, paths in self._namespaced_source_paths.items():
                for path in paths:
                    if not path.exists():
                        continue
                    try:
                        items = list(path.iterdir())
                    except Exception:
                        continue
                    for item in items:
                        if not item.is_dir():
                            continue
                        with contextlib.suppress(Exception):
                            self._load_skill(item, namespace=alias)  # Per-skill isolation (R9)

            self._validate_deps()

            # Success — clear dirty flag
            self._dirty = False
        except Exception as exc:
            # Failure — restore old references, leave dirty for retry
            _log.error(
                "Failed to refresh skills registry: err={err}",
                err=str(exc),
            )
            self._agents = old_agents
            self._skills = old_skills
            self._workflows = old_workflows
            # _dirty remains True for next retry

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

    def _load_skill(self, skill_dir: Path, namespace: str | None = None) -> None:
        """Load a single skill and its agents.

        Args:
            skill_dir: Path to the skill directory.
            namespace: Optional plugin alias for namespaced skills.
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
        description = post.metadata.get("description", "")
        version = post.metadata.get("version")
        name = skill_dir.name

        if not description or not isinstance(description, str):
            _log.warning(
                "Skill has missing or invalid description: skill={skill}",
                skill=name,
            )
            return

        # Store skill metadata (version from YAML is object, need to cast)
        version_str: str | None = version if isinstance(version, str) else None

        raw_depends_on = post.metadata.get("depends_on")

        if raw_depends_on is None:
            depends_on: tuple[str, ...] = ()
        elif isinstance(raw_depends_on, list) and all(isinstance(d, str) for d in raw_depends_on):
            # Per-element isinstance in the comprehension narrows `object` → `str` for ty.
            depends_on = tuple(d for d in raw_depends_on if isinstance(d, str))
        else:
            _log.warning(
                "Skill has invalid depends_on (expected list of strings), treating as empty: "
                "skill={skill}",
                skill=name,
            )
            depends_on = ()

        skill = Skill(
            name=name,
            description=description,
            body=post.content.strip(),
            path=skill_dir,
            version=version_str,
            depends_on=depends_on,
            namespace=namespace,
        )

        qname = skill.qualified_name

        if qname in self._skills:
            prefix = f"{qname}/"
            for ns in [k for k in self._agents if k.startswith(prefix)]:
                del self._agents[ns]

            # Clear previous workflow entries for this skill
            for key in [k for k in self._workflows if k[0] == qname]:
                del self._workflows[key]

            _log.debug("Replacing skill from earlier source: name={name}", name=qname)

        self._skills[qname] = skill

        _log.debug(
            "Loaded skill: name={name}, description={desc}",
            name=qname,
            desc=description[:50] + "..." if len(description) > 50 else description,
        )

        # Load workflows if workflows/ directory exists
        workflows = load_workflows(skill_dir, name)

        for workflow_name, workflow_def in workflows.items():
            self._workflows[(qname, workflow_name)] = workflow_def

        # Load agents if agents/ directory exists
        agents_dir = skill_dir / "agents"
        if agents_dir.exists() and agents_dir.is_dir():
            self._load_agents(agents_dir, qname)

    def _load_agents(self, agents_dir: Path, skill_name: str) -> None:
        """Load all agents from a skill's agents/ directory.

        Args:
            agents_dir: Path to the agents/ directory.
            skill_name: Qualified name of the parent skill (for namespacing).
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
            skill_name: Qualified name of the parent skill (for namespacing).
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
