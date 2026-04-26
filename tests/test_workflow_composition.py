"""Tests for workflow composition: resolve_composes, cycle detection, reference validation.

Pure unit tests — no DB, no SDK, no async.
"""

from pathlib import Path

import pytest

from tachikoma.workflows.composition import (
    CascadeOutcome,
    CreateChild,
    MutationBatch,
    SoftDelete,
    UpdateState,
    detect_cycles,
    resolve_composes,
    validate_references,
)
from tachikoma.workflows.definition import StepDefinition, WorkflowDefinition


def _make_step(
    step_id: str,
    title: str = "Step",
    *,
    composes: str | None = None,
) -> StepDefinition:
    return StepDefinition(
        id=step_id,
        title=title,
        instructions_path=Path(f"/{step_id}/instructions.md"),
        references_path=None,
        scripts_path=None,
        composes=composes,
    )


def _make_workflow(
    skill_name: str,
    workflow_name: str,
    steps: list[StepDefinition] | None = None,
) -> WorkflowDefinition:
    return WorkflowDefinition(
        skill_name=skill_name,
        workflow_name=workflow_name,
        steps=steps or [],
        path=Path(f"/{skill_name}/workflows/{workflow_name}"),
    )


# ---------------------------------------------------------------------------
# resolve_composes
# ---------------------------------------------------------------------------


class TestResolveComposes:
    def test_same_skill_reference(self) -> None:
        skill, wf = resolve_composes("process-inbox", "review-skill")
        assert skill == "review-skill"
        assert wf == "process-inbox"

    def test_cross_skill_reference(self) -> None:
        skill, wf = resolve_composes("other-skill/process-inbox", "review-skill")
        assert skill == "other-skill"
        assert wf == "process-inbox"

    def test_empty_string_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            resolve_composes("", "skill")

    def test_whitespace_only_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            resolve_composes("   ", "skill")

    def test_leading_slash_raises(self) -> None:
        with pytest.raises(ValueError, match="Malformed"):
            resolve_composes("/foo", "skill")

    def test_trailing_slash_raises(self) -> None:
        with pytest.raises(ValueError, match="Malformed"):
            resolve_composes("foo/", "skill")

    def test_multiple_slashes_raises(self) -> None:
        with pytest.raises(ValueError, match="multiple slashes"):
            resolve_composes("a/b/c", "skill")

    def test_whitespace_is_stripped(self) -> None:
        skill, wf = resolve_composes("  process-inbox  ", "review-skill")
        assert skill == "review-skill"
        assert wf == "process-inbox"


# ---------------------------------------------------------------------------
# detect_cycles
# ---------------------------------------------------------------------------


class TestDetectCycles:
    def test_acyclic_graph_no_cycles(self) -> None:
        a = _make_workflow("s", "A", [_make_step("01", composes="B")])
        b = _make_workflow("s", "B", [_make_step("01", composes="C")])
        c = _make_workflow("s", "C")
        sccs = detect_cycles({("s", "A"): a, ("s", "B"): b, ("s", "C"): c})
        assert sccs == []

    def test_self_loop_detected(self) -> None:
        a = _make_workflow("s", "A", [_make_step("01", composes="A")])
        sccs = detect_cycles({("s", "A"): a})
        assert len(sccs) == 1
        assert ("s", "A") in sccs[0]

    def test_mutual_cycle_detected(self) -> None:
        a = _make_workflow("s", "A", [_make_step("01", composes="B")])
        b = _make_workflow("s", "B", [_make_step("01", composes="A")])
        sccs = detect_cycles({("s", "A"): a, ("s", "B"): b})
        assert len(sccs) == 1
        cycle_set = set(sccs[0])
        assert cycle_set == {("s", "A"), ("s", "B")}

    def test_cycle_plus_dangling_branch(self) -> None:
        a = _make_workflow("s", "A", [_make_step("01", composes="B")])
        b = _make_workflow("s", "B", [_make_step("01", composes="A")])
        c = _make_workflow("s", "C", [_make_step("01", composes="A")])
        sccs = detect_cycles({("s", "A"): a, ("s", "B"): b, ("s", "C"): c})
        # Only A and B are in the cycle; C is a dangling branch pointing into the cycle
        assert len(sccs) == 1
        cycle_set = set(sccs[0])
        assert cycle_set == {("s", "A"), ("s", "B")}

    def test_disconnected_graph_no_cycles(self) -> None:
        a = _make_workflow("s", "A")
        b = _make_workflow("s", "B")
        sccs = detect_cycles({("s", "A"): a, ("s", "B"): b})
        assert sccs == []

    def test_malformed_composes_skipped(self) -> None:
        a = _make_workflow("s", "A", [_make_step("01", composes="/invalid")])
        b = _make_workflow("s", "B")
        sccs = detect_cycles({("s", "A"): a, ("s", "B"): b})
        assert sccs == []

    def test_cross_skill_cycle_detected(self) -> None:
        a = _make_workflow("s1", "A", [_make_step("01", composes="s2/B")])
        b = _make_workflow("s2", "B", [_make_step("01", composes="s1/A")])
        sccs = detect_cycles({("s1", "A"): a, ("s2", "B"): b})
        assert len(sccs) == 1
        cycle_set = set(sccs[0])
        assert cycle_set == {("s1", "A"), ("s2", "B")}


# ---------------------------------------------------------------------------
# validate_references
# ---------------------------------------------------------------------------


class TestValidateReferences:
    def test_missing_target_rejects_parent(self) -> None:
        a = _make_workflow("s", "A", [_make_step("01", composes="nonexistent")])
        rejected = validate_references({("s", "A"): a}, already_rejected=set())
        assert ("s", "A") in rejected

    def test_zero_step_target_rejects_parent(self) -> None:
        a = _make_workflow("s", "A", [_make_step("01", composes="B")])
        b = _make_workflow("s", "B", steps=[])  # zero steps
        rejected = validate_references({("s", "A"): a, ("s", "B"): b}, already_rejected=set())
        assert ("s", "A") in rejected

    def test_cascading_rejection(self) -> None:
        a = _make_workflow("s", "A", [_make_step("01", composes="B")])
        b = _make_workflow("s", "B", [_make_step("01", composes="C")])
        c = _make_workflow("s", "C", [_make_step("01", composes="nonexistent")])
        rejected = validate_references(
            {("s", "A"): a, ("s", "B"): b, ("s", "C"): c},
            already_rejected=set(),
        )
        # C is rejected first (missing target), then B (target rejected), then A (target rejected)
        assert rejected == {("s", "A"), ("s", "B"), ("s", "C")}

    def test_cross_skill_missing_target(self) -> None:
        a = _make_workflow("s", "A", [_make_step("01", composes="other-skill/missing")])
        rejected = validate_references({("s", "A"): a}, already_rejected=set())
        assert ("s", "A") in rejected

    def test_malformed_composes_rejects_parent(self) -> None:
        a = _make_workflow("s", "A", [_make_step("01", composes="/invalid")])
        rejected = validate_references({("s", "A"): a}, already_rejected=set())
        assert ("s", "A") in rejected

    def test_valid_reference_not_rejected(self) -> None:
        a = _make_workflow("s", "A", [_make_step("01", composes="B")])
        b = _make_workflow("s", "B", [_make_step("01")])
        rejected = validate_references({("s", "A"): a, ("s", "B"): b}, already_rejected=set())
        assert rejected == set()

    def test_already_rejected_propagated(self) -> None:
        a = _make_workflow("s", "A", [_make_step("01", composes="B")])
        b = _make_workflow("s", "B", [_make_step("01")])
        # B is already rejected (e.g., from cycle detection)
        rejected = validate_references(
            {("s", "A"): a, ("s", "B"): b},
            already_rejected={("s", "B")},
        )
        assert ("s", "A") in rejected

    def test_step_without_composes_not_affected(self) -> None:
        a = _make_workflow("s", "A", [_make_step("01")])  # no composes
        rejected = validate_references({("s", "A"): a}, already_rejected=set())
        assert rejected == set()


# ---------------------------------------------------------------------------
# MutationBatch / CascadeOutcome dataclasses
# ---------------------------------------------------------------------------


class TestMutationDataclasses:
    def test_mutation_batch_starts_empty(self) -> None:
        batch = MutationBatch()
        assert batch.ordered == []

    def test_cascade_outcome_construction(self) -> None:
        outcome = CascadeOutcome(
            deepest_layer_id="id-1",
            active_step_id="01-step",
            condition_skips=[],
            finalized_top_level=False,
        )
        assert outcome.deepest_layer_id == "id-1"
        assert outcome.active_step_id == "01-step"

    def test_update_state_construction(self) -> None:
        m = UpdateState(layer_id="id", step_states={"01": "started"}, current_step="01")
        assert m.layer_id == "id"

    def test_create_child_construction(self) -> None:
        m = CreateChild(
            child_id="c1",
            parent_id="p1",
            parent_step_id="02",
            skill_name="s",
            workflow_name="w",
            step_states={"01": "pending"},
            definition_snapshot=[],
            scratchpad_path="/scratch.md",
        )
        assert m.parent_id == "p1"

    def test_soft_delete_construction(self) -> None:
        m = SoftDelete(layer_id="id")
        assert m.layer_id == "id"
