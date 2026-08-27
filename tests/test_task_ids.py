"""One canonical task-id lexical contract, everywhere.

The fresh spec-aligned smoke produced a routable, well-formed plan whose task
ids were T1..T7. Planning and routing accepted it; the deliverable layer then
refused the very first workspace with "unsafe task id for workspace: 'T1'",
because TaskContract required only a non-empty id while the workspace rule is
lowercase-only. Same defect genus as the original capability-contract gap:
a downstream layer enforcing a rule the planner neither states nor checks.

These tests pin the single source of truth: domain.TASK_ID_PATTERN, used by
TaskContract, the planner prompt AND post-decode validation, and the
deliverable workspace boundary.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from doom_team.deliverable import DeliverableError, DeliverableRepo
from doom_team.domain import (
    TASK_ID_PATTERN,
    TASK_ID_RULE,
    Mission,
    Objective,
    TaskContract,
    task_id_is_valid,
)
from doom_team.planner import PLANNER_SYSTEM_PROFILE, Planner, PlanningError
from tests.test_planner import FakeInference, _plan_json, _task

VALID_IDS = ("t1", "task-1", "task_1", "task.http", "implement-http", "a", "0x1")
INVALID_IDS = (
    "T1",
    "Task-1",
    "task 1",
    "/task",
    "task/name",
    "task@name",
    "",
    "-task",
    ".task",
    "x" * 82,
)


def _contract(task_id: str, deps: tuple[str, ...] = ()) -> TaskContract:
    return TaskContract(
        id=task_id,
        objective_id="obj",
        instruction="do it",
        acceptance_criteria=("done",),
        dependencies=deps,
    )


# --- the single source of truth -------------------------------------------


def test_pattern_and_prose_rule_agree() -> None:
    """TASK_ID_RULE is shown to models and operators; it must not drift from
    the pattern it describes."""
    assert TASK_ID_PATTERN.pattern == r"^[a-z0-9][a-z0-9._-]{0,80}$"
    for token in ("lowercase", "digit", "81"):
        assert token in TASK_ID_RULE


def test_deliverable_uses_the_canonical_pattern_object() -> None:
    """Not a copy: the workspace boundary holds the same compiled pattern, so
    the two layers cannot silently diverge again."""
    import doom_team.deliverable as deliverable

    assert deliverable._TASK_ID is TASK_ID_PATTERN


@pytest.mark.parametrize("task_id", VALID_IDS)
def test_valid_ids_accepted_everywhere(task_id: str, tmp_path: Path) -> None:
    assert task_id_is_valid(task_id)
    contract = _contract(task_id)
    assert contract.id == task_id
    repo = DeliverableRepo.create(tmp_path / "repo")
    workspace = repo.create_task_workspace(task_id, tmp_path / "work")
    assert workspace.task_id == task_id


@pytest.mark.parametrize("task_id", INVALID_IDS)
def test_invalid_ids_rejected_by_the_domain(task_id: str) -> None:
    assert not task_id_is_valid(task_id)
    with pytest.raises(ValueError):
        _contract(task_id)


@pytest.mark.parametrize("task_id", ["T1", "task 1", "task/name"])
def test_workspace_still_independently_rejects_invalid_ids(
    task_id: str, tmp_path: Path
) -> None:
    """Defense in depth: the planner should catch these first, but the
    workspace boundary keeps its own fail-closed check."""
    repo = DeliverableRepo.create(tmp_path / "repo")
    with pytest.raises(DeliverableError, match="unsafe task id"):
        repo.create_task_workspace(task_id, tmp_path / "work")


# --- dependencies share the vocabulary, exactly and case-sensitively -------


def test_dependency_with_invalid_id_rejected() -> None:
    with pytest.raises(ValueError, match="invalid"):
        _contract("task-b", deps=("T1",))


def test_case_mismatched_dependency_cannot_resolve() -> None:
    # "task-A" is lexically invalid, so it dies at the contract; and even a
    # lexically valid-but-different id dies at Mission dependency resolution.
    with pytest.raises(ValueError):
        _contract("task-b", deps=("task-A",))
    with pytest.raises(ValueError, match="unknown task"):
        Mission(
            id="m",
            objective=Objective(id="obj", description="d"),
            tasks=(
                TaskContract(
                    id="task-b",
                    objective_id="obj",
                    instruction="x",
                    acceptance_criteria=("y",),
                    dependencies=("task-c",),
                ),
            ),
        )


def test_valid_exact_dependencies_accepted() -> None:
    contract = _contract("task-b", deps=("task-a",))
    assert contract.dependencies == ("task-a",)


# --- the planner states the rule and enforces it before Mission acceptance -


def test_planner_prompt_states_the_task_id_contract() -> None:
    assert TASK_ID_RULE in PLANNER_SYSTEM_PROFILE


def test_smoke_shaped_uppercase_plan_rejected_before_mission_acceptance() -> None:
    """The exact rerun-4 shape: valid capabilities, valid DAG, ids T1/T2.
    The plan must fail at planning - no Mission, no workspace, no transport."""
    plan = _plan_json([
        _task("T1", ["backend.python"]),
        _task("T2", ["backend.python", "database.sqlite"], deps=["T1"]),
    ])
    fake = FakeInference(plan)
    with pytest.raises(PlanningError, match="not a valid task id"):
        Planner(fake).plan("Build a small service")


def test_lowercase_equivalent_plan_accepted_and_workspace_compatible(
    tmp_path: Path,
) -> None:
    plan = _plan_json([
        _task("t1", ["backend.python"]),
        _task("t2", ["backend.python", "database.sqlite"], deps=["t1"]),
    ])
    mission = Planner(FakeInference(plan)).plan("Build a small service")
    assert sorted(t.id for t in mission.tasks) == ["t1", "t2"]
    repo = DeliverableRepo.create(tmp_path / "repo")
    for task in mission.tasks:
        repo.create_task_workspace(task.id, tmp_path / "work")


def test_invalid_dependency_reference_rejected_at_planning() -> None:
    plan = _plan_json([
        _task("t1", ["backend.python"]),
        _task("t2", ["backend.python"], deps=["T1"]),
    ])
    with pytest.raises(PlanningError, match="dependencies"):
        Planner(FakeInference(plan)).plan("Build a small service")


@pytest.mark.parametrize("bad", ["task 1", "task/name", "x" * 82])
def test_other_invalid_planner_ids_rejected(bad: str) -> None:
    plan = _plan_json([_task(bad, ["backend.python"])])
    with pytest.raises(PlanningError):
        Planner(FakeInference(plan)).plan("Build a small service")


def test_no_normalization_happens() -> None:
    """The planner must reject 'T1', never quietly rewrite it to 't1' -
    rewriting could collide ids and re-point dependency references."""
    plan = _plan_json([
        _task("T1", ["backend.python"]),
        _task("t1", ["backend.python"]),
    ])
    with pytest.raises(PlanningError):
        Planner(FakeInference(plan)).plan("Build a small service")
