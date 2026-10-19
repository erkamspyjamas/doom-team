"""Planner prompt bounds match enforced parsing limits, including custom config."""
import json

import pytest

from doom_team.planner import (
    MAX_CRITERIA_PER_TASK,
    MAX_CRITERION_CHARS,
    MAX_IDENTIFIER_CHARS,
    PLANNER_SYSTEM_PROFILE,
    Planner,
    PlannerConfig,
    PlanningError,
)


class Inference:
    def __init__(self, tasks: list[dict[str, object]]) -> None:
        self.content = json.dumps({"tasks": tasks})
        self.messages: list[dict[str, str]] = []
        self.call_count = 0

    def complete(self, messages: list[dict[str, str]]) -> object:
        self.call_count += 1
        self.messages = messages
        return type("Response", (), {"content": self.content})()


def task(task_id: str = "implement") -> dict[str, object]:
    return {
        "task_id": task_id,
        "description": "Implement the requested behavior and passing regression tests.",
        "dependencies": [],
        "required_capabilities": ["backend.python"],
        "acceptance_criteria": ["The behavior and regression suite pass."],
    }


def test_default_sent_profile_discloses_all_enforced_size_limits() -> None:
    inference = Inference([task()])
    Planner(inference).plan("Build the requested service")
    system = inference.messages[0]["content"]
    assert system == PLANNER_SYSTEM_PROFILE
    assert "Use at most 10 tasks." in system
    assert "description: at most 2000 characters" in system
    assert "dependencies: at most 8 entries" in system
    assert f"at most {MAX_IDENTIFIER_CHARS} characters each" in system
    assert f"acceptance_criteria: 1..{MAX_CRITERIA_PER_TASK} entries" in system
    assert f"EACH at most {MAX_CRITERION_CHARS} characters" in system
    assert inference.messages[1] == {"role": "user", "content": "Build the requested service"}


def test_custom_config_is_disclosed_without_stale_default_limits() -> None:
    inference = Inference([task()])
    config = PlannerConfig(max_tasks=2, max_description_chars=123, max_dependencies=1)
    Planner(inference, config=config).plan("Build it")
    system = inference.messages[0]["content"]
    assert "Use at most 2 tasks." in system
    assert "Use at most 10 tasks." not in system
    assert "description: at most 123 characters" in system
    assert "description: at most 2000 characters" not in system
    assert "dependencies: at most 1 entries" in system
    assert "dependencies: at most 8 entries" not in system
    assert f"EACH at most {MAX_CRITERION_CHARS} characters" in system


@pytest.mark.parametrize("length", [499, 500, 501])
def test_criterion_boundary_remains_fail_closed_without_replanning(length: int) -> None:
    row = task()
    row["acceptance_criteria"] = ["x" * length]
    inference = Inference([row])
    planner = Planner(inference)
    if length <= MAX_CRITERION_CHARS:
        mission = planner.plan("Build it")
        assert mission.tasks[0].acceptance_criteria == ("x" * length,)
    else:
        with pytest.raises(PlanningError, match="acceptance_criteria entries exceed the length bound"):
            planner.plan("Build it")
    assert inference.call_count == 1


@pytest.mark.parametrize("count", [8, 9])
def test_criterion_count_is_not_relaxed(count: int) -> None:
    row = task()
    row["acceptance_criteria"] = [f"Behavior {i} passes" for i in range(count)]
    planner = Planner(Inference([row]))
    if count == MAX_CRITERIA_PER_TASK:
        assert len(planner.plan("Build it").tasks[0].acceptance_criteria) == count
    else:
        with pytest.raises(PlanningError, match="acceptance-criteria bound"):
            planner.plan("Build it")


@pytest.mark.parametrize("length", [123, 124])
def test_custom_description_boundary_remains_enforced(length: int) -> None:
    row = task()
    row["description"] = "x" * length
    planner = Planner(Inference([row]), config=PlannerConfig(max_description_chars=123))
    if length == 123:
        assert len(planner.plan("Build it").tasks[0].instruction) == length
    else:
        with pytest.raises(PlanningError, match="description exceeds the length bound"):
            planner.plan("Build it")


def test_custom_task_and_dependency_bounds_remain_enforced() -> None:
    rows = [task("first"), task("second"), task("third")]
    with pytest.raises(PlanningError, match="2-task bound"):
        Planner(Inference(rows), config=PlannerConfig(max_tasks=2)).plan("Build it")
    rows[-1]["dependencies"] = ["first", "second"]
    with pytest.raises(PlanningError, match="dependency bound"):
        Planner(Inference(rows), config=PlannerConfig(max_dependencies=1)).plan("Build it")
