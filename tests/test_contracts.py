"""Tests for Objective, Mission, and TaskContract validation/canonicalization."""

import pytest

from doom_team.domain import Mission, Objective, TaskContract


def _make_objective(obj_id: str = "obj-1", description: str = "Build foundation") -> Objective:
    return Objective(id=obj_id, description=description)


def _make_task(
    task_id: str = "task-1",
    objective_id: str = "obj-1",
    instruction: str = "Bootstrap",
    acceptance_criteria: tuple[str, ...] = ("c1", "c2"),
    dependencies: tuple[str, ...] = (),
) -> TaskContract:
    return TaskContract(
        id=task_id,
        objective_id=objective_id,
        instruction=instruction,
        acceptance_criteria=acceptance_criteria,
        dependencies=dependencies,
    )


# Canonicalization tests


def test_objective_canonicalizes_trimmed_fields() -> None:
    obj = _make_objective(obj_id="  obj-1  ", description="  Build foundation  ")
    assert obj.id == "obj-1"
    assert obj.description == "Build foundation"


def test_task_contract_canonicalizes_fields_and_sorts() -> None:
    task = _make_task(
        task_id="  task-1  ",
        objective_id="  obj-1  ",
        instruction="  Bootstrap  ",
        acceptance_criteria=("  c2  ", "c1"),
        dependencies=("task-3", "task-2"),
    )
    assert task.id == "task-1"
    assert task.objective_id == "obj-1"
    assert task.instruction == "Bootstrap"
    assert task.acceptance_criteria == ("c1", "c2")
    assert task.dependencies == ("task-2", "task-3")


def test_mission_canonicalizes_tasks_sorted() -> None:
    obj = _make_objective()
    task_b = _make_task(task_id="task-b", dependencies=("task-a",))
    task_a = _make_task(task_id="task-a")
    mission = Mission(id="mission-1", objective=obj, tasks=(task_b, task_a))
    assert mission.id == "mission-1"
    assert [t.id for t in mission.tasks] == ["task-a", "task-b"]


# Invalid input tests


def test_invalid_objective_empty_id_rejected() -> None:
    with pytest.raises(ValueError, match="Objective id"):
        _make_objective(obj_id="  ")


def test_invalid_objective_empty_description_rejected() -> None:
    with pytest.raises(ValueError, match="Objective description"):
        _make_objective(description="")


def test_invalid_task_empty_instruction_rejected() -> None:
    with pytest.raises(ValueError, match="Instruction"):
        _make_task(instruction="  ")


def test_invalid_task_empty_acceptance_criteria_rejected() -> None:
    with pytest.raises(ValueError, match="Acceptance criteria"):
        _make_task(acceptance_criteria=())


def test_invalid_task_self_dependency_rejected() -> None:
    with pytest.raises(ValueError, match="cannot depend on itself"):
        _make_task(dependencies=("task-1",))


def test_invalid_task_duplicate_dependency_rejected() -> None:
    with pytest.raises(ValueError, match="Dependencies must be unique"):
        _make_task(dependencies=("task-2", "task-2"))


def test_invalid_mission_unknown_dependency_rejected() -> None:
    obj = _make_objective()
    task = _make_task(task_id="task-1", dependencies=("task-2",))
    with pytest.raises(ValueError, match="depends on unknown task"):
        Mission(id="mission-1", objective=obj, tasks=(task,))


def test_invalid_mission_cyclic_dependency_rejected() -> None:
    obj = _make_objective()
    task_a = _make_task(task_id="task-a", dependencies=("task-b",))
    task_b = _make_task(task_id="task-b", dependencies=("task-a",))
    with pytest.raises(ValueError, match="Cyclic dependency"):
        Mission(id="mission-1", objective=obj, tasks=(task_a, task_b))


def test_invalid_mission_duplicate_task_ids_rejected() -> None:
    obj = _make_objective()
    task1 = _make_task(task_id="task-1")
    task2 = _make_task(task_id="task-1")
    with pytest.raises(ValueError, match="Task ids must be unique"):
        Mission(id="mission-1", objective=obj, tasks=(task1, task2))


def test_invalid_mission_objective_mismatch_rejected() -> None:
    obj = _make_objective()
    task = _make_task(objective_id="other-obj")
    with pytest.raises(ValueError, match="objective_id does not match"):
        Mission(id="mission-1", objective=obj, tasks=(task,))


def test_invalid_mission_empty_tasks_rejected() -> None:
    obj = _make_objective()
    with pytest.raises(ValueError, match="at least one task"):
        Mission(id="mission-1", objective=obj, tasks=())


# Valid dependency graph


def test_valid_mission_with_dependencies_passes() -> None:
    obj = _make_objective()
    task_a = _make_task(task_id="task-a")
    task_b = _make_task(task_id="task-b", dependencies=("task-a",))
    task_c = _make_task(task_id="task-c", dependencies=("task-a", "task-b"))
    mission = Mission(id="mission-1", objective=obj, tasks=(task_c, task_a, task_b))
    assert [t.id for t in mission.tasks] == ["task-a", "task-b", "task-c"]
