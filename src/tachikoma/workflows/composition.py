"""Pure composition helpers and in-memory dataclasses for workflow composition.

No SDK / DB / async dependencies. Keeps validation testable in isolation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from loguru import logger

from tachikoma.workflows.definition import StepDefinition, WorkflowDefinition

_log = logger.bind(component="workflows")


# ---------------------------------------------------------------------------
# Resolution helper
# ---------------------------------------------------------------------------

_VERTEX = tuple[str, str]  # (skill_name, workflow_name)


def resolve_composes(composes_str: str, parent_skill_name: str) -> tuple[str, str]:
    """Resolve a ``composes`` frontmatter value to ``(skill_name, workflow_name)``.

    Same-skill: ``"process-inbox-note"`` → ``(parent_skill_name, "process-inbox-note")``
    Cross-skill: ``"review-skill/process-inbox-note"`` → ``("review-skill", "process-inbox-note")``

    Raises ``ValueError`` on empty or malformed input.
    """
    if not composes_str or not composes_str.strip():
        raise ValueError("composes value must be a non-empty string")

    composes_str = composes_str.strip()

    if "/" in composes_str:
        parts = composes_str.split("/", 1)
        skill, workflow = parts[0], parts[1]
        if not skill or not workflow:
            raise ValueError(f"Malformed composes value: '{composes_str}'")
        # Reject values with additional slashes inside the workflow name
        if "/" in workflow:
            raise ValueError(f"Malformed composes value (multiple slashes): '{composes_str}'")
        return skill, workflow

    return parent_skill_name, composes_str


def _composition_edge(step: StepDefinition) -> str | None:
    """Yield the composition edge value for a step (composes or loop, at most one).

    Mutex enforcement runs upstream in the registry pre-pass, guaranteeing at
    most one of the two is set for any surviving step.
    """
    if step.composes:
        return step.composes
    if step.loop:
        return step.loop
    return None


# ---------------------------------------------------------------------------
# Cycle detection (three-color DFS)
# ---------------------------------------------------------------------------

def detect_cycles(
    workflows: dict[_VERTEX, WorkflowDefinition],
) -> list[list[_VERTEX]]:
    """Return a list of strongly-connected components (each a list of vertices).

    A single self-loop is one SCC of size 1; a mutual cycle A↔B is one SCC
    of size 2.  Non-cyclic workflows are not returned.

    Builds the adjacency map by iterating each workflow's steps and resolving
    ``composes`` fields.  Steps whose ``composes`` value is malformed are
    skipped (caught later by reference validation).
    """
    WHITE, GRAY, BLACK = 0, 1, 2  # noqa: N806

    # Build adjacency map
    adj: dict[_VERTEX, list[_VERTEX]] = {v: [] for v in workflows}

    for (skill, wf_name), wf_def in workflows.items():
        for step in wf_def.steps:
            edge = _composition_edge(step)
            if not edge:
                continue
            try:
                target_skill, target_wf = resolve_composes(edge, skill)
            except ValueError:
                continue
            target = (target_skill, target_wf)
            # Only add edges to vertices in the graph (self-loops are valid)
            if target in workflows:
                adj[(skill, wf_name)].append(target)

    color: dict[_VERTEX, int] = dict.fromkeys(workflows, WHITE)
    stack: list[_VERTEX] = []
    sccs: list[list[_VERTEX]] = []

    def dfs(v: _VERTEX) -> None:
        color[v] = GRAY
        stack.append(v)
        for neighbour in adj.get(v, []):
            if color[neighbour] == GRAY:
                # Found a back-edge — extract the cycle
                idx = stack.index(neighbour)
                sccs.append(list(stack[idx:]))
            elif color[neighbour] == WHITE:
                dfs(neighbour)
        stack.pop()
        color[v] = BLACK

    for v in workflows:
        if color[v] == WHITE:
            dfs(v)

    return sccs


# ---------------------------------------------------------------------------
# Reference validation (fixed-point iteration)
# ---------------------------------------------------------------------------

def validate_references(
    workflows: dict[_VERTEX, WorkflowDefinition],
    already_rejected: set[_VERTEX],
) -> set[_VERTEX]:
    """Return the set of newly-rejected vertices (callers union with ``already_rejected``).

    A workflow is rejected when one of its composition steps references a
    target that is missing, already rejected, or has zero steps.  Cascading
    rejection converges via fixed-point iteration.
    """
    rejected = set(already_rejected)

    changed = True
    while changed:
        changed = False
        for vertex, wf_def in list(workflows.items()):
            if vertex in rejected:
                continue
            for step in wf_def.steps:
                edge = _composition_edge(step)
                if not edge:
                    continue
                edge_kind = "loop" if step.loop else "composes"
                try:
                    target_skill, target_wf = resolve_composes(
                        edge, vertex[0]
                    )
                except ValueError:
                    _log.warning(
                        "Workflow rejected: malformed {kind} value: "
                        "skill={skill}, workflow={workflow}, step={step}, value={value}",
                        kind=edge_kind,
                        skill=vertex[0],
                        workflow=vertex[1],
                        step=step.id,
                        value=edge,
                    )
                    rejected.add(vertex)
                    changed = True
                    break

                target = (target_skill, target_wf)

                if target not in workflows:
                    _log.warning(
                        "Workflow rejected: {kind} target not found: "
                        "skill={skill}, workflow={workflow}, step={step}, "
                        "target_skill={ts}, target_workflow={tw}",
                        kind=edge_kind,
                        skill=vertex[0],
                        workflow=vertex[1],
                        step=step.id,
                        ts=target_skill,
                        tw=target_wf,
                    )
                    rejected.add(vertex)
                    changed = True
                    break

                if target in rejected:
                    _log.warning(
                        "Workflow rejected: {kind} target was itself rejected: "
                        "skill={skill}, workflow={workflow}, step={step}, "
                        "target_skill={ts}, target_workflow={tw}",
                        kind=edge_kind,
                        skill=vertex[0],
                        workflow=vertex[1],
                        step=step.id,
                        ts=target_skill,
                        tw=target_wf,
                    )
                    rejected.add(vertex)
                    changed = True
                    break

                target_def = workflows[target]
                if not target_def.steps:
                    _log.warning(
                        "Workflow rejected: {kind} target has zero steps: "
                        "skill={skill}, workflow={workflow}, step={step}, "
                        "target_skill={ts}, target_workflow={tw}",
                        kind=edge_kind,
                        skill=vertex[0],
                        workflow=vertex[1],
                        step=step.id,
                        ts=target_skill,
                        tw=target_wf,
                    )
                    rejected.add(vertex)
                    changed = True
                    break

    # Return only the newly-rejected ones
    return rejected - already_rejected


# ---------------------------------------------------------------------------
# In-memory mutation dataclasses (used by cascade engine + repository)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class UpdateState:
    """Queued update to an existing workflow state record."""

    layer_id: str
    step_states: dict[str, str]
    current_step: str | None
    loop_state: dict | None = None


@dataclass(frozen=True)
class CreateChild:
    """Queued creation of a child workflow state record."""

    child_id: str
    parent_id: str
    parent_step_id: str
    skill_name: str
    workflow_name: str
    step_states: dict[str, str]
    definition_snapshot: list[dict]
    scratchpad_path: str


@dataclass(frozen=True)
class SoftDelete:
    """Queued soft-delete of a workflow state record."""

    layer_id: str


@dataclass
class MutationBatch:
    """Ordered list of mutations staged in memory during cascade evaluation.

    Applied atomically by ``repository.apply_mutation_batch``.
    """

    ordered: list[UpdateState | CreateChild | SoftDelete] = field(default_factory=list)


@dataclass(frozen=True)
class CascadeOutcome:
    """Result of the cascade loop — drives response construction."""

    deepest_layer_id: str
    active_step_id: str | None  # None when top-level finalized
    condition_skips: list[tuple[str, str, str]]  # (workflow_name, step_id, reason)
    finalized_top_level: bool
    halted_at_loop_step: str | None = None  # step_id of loop step that halted auto-advance
