"""Tests for the objective planner: strict JSON in, validated Mission out."""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from doom_team.domain import Mission
from doom_team.planner import Planner, PlannerConfig, PlanningError


@dataclass
class _Response:
    content: str
    model: str = "fake-model"
    provider: str = "fake-provider"


class FakeInference:
    """Deterministic InferencePort double returning a canned response."""

    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[list[dict[str, str]]] = []

    def complete(self, messages: list[dict[str, str]]) -> _Response:
        self.calls.append(messages)
        return _Response(content=self.content)


def url_shortener_plan() -> dict:
    return {
        "tasks": [
            {
                "task_id": "api-foundation",
                "description": "Implement the URL shortener REST API skeleton with POST /urls and GET /{short_code}",
                "dependencies": [],
                "required_capabilities": ["backend.python"],
                "acceptance_criteria": ["POST /urls and GET /{short_code} handlers exist"],
            },
            {
                "task_id": "sqlite-storage",
                "description": "Persist short codes and target URLs in SQLite with parameterized queries",
                "dependencies": ["api-foundation"],
                "required_capabilities": ["database.sqlite", "backend.python"],
                "acceptance_criteria": ["mappings survive process restart"],
            },
            {
                "task_id": "automated-tests",
                "description": "Add automated tests covering create, resolve, and missing-code paths",
                "dependencies": ["sqlite-storage"],
                "required_capabilities": ["testing"],
                "acceptance_criteria": ["python -m pytest passes"],
            },
            {
                "task_id": "readme-docs",
                "description": "Write a README with fresh-install and run instructions",
                "dependencies": ["automated-tests"],
                "required_capabilities": ["documentation"],
                "acceptance_criteria": ["README documents install, run, and test verbatim"],
            },
        ]
    }


OBJECTIVE = (
    "Build a small URL shortener REST API in Python. Use SQLite. "
    "Provide POST /urls and GET /{short_code}. "
    "Include automated tests and a README."
)


def plan_with(content: str, objective: str = OBJECTIVE) -> Mission:
    return Planner(FakeInference(content)).plan(objective)


def test_valid_strict_plan_accepted_and_capabilities_propagated() -> None:
    mission = plan_with(json.dumps(url_shortener_plan()))
    assert isinstance(mission, Mission)
    assert len(mission.tasks) == 4
    by_id = {task.id: task for task in mission.tasks}
    assert by_id["sqlite-storage"].required_capabilities == (
        "backend.python",
        "database.sqlite",
    )
    assert by_id["sqlite-storage"].dependencies == ("api-foundation",)
    assert by_id["readme-docs"].required_capabilities == ("documentation",)


def test_planner_sends_objective_and_system_profile() -> None:
    fake = FakeInference(json.dumps(url_shortener_plan()))
    Planner(fake).plan(OBJECTIVE)
    (messages,) = fake.calls
    assert messages[0]["role"] == "system"
    assert "STRICT JSON" in messages[0]["content"]
    assert messages[1] == {"role": "user", "content": OBJECTIVE}


def test_malformed_json_rejected() -> None:
    with pytest.raises(PlanningError, match="not valid JSON"):
        plan_with("Sure! Here is the plan:\n```json\n{}\n```")


def test_duplicate_task_ids_rejected() -> None:
    plan = url_shortener_plan()
    plan["tasks"][1]["task_id"] = "api-foundation"
    plan["tasks"][1]["dependencies"] = []
    with pytest.raises(PlanningError, match="invariants"):
        plan_with(json.dumps(plan))


def test_unknown_dependency_rejected() -> None:
    plan = url_shortener_plan()
    plan["tasks"][1]["dependencies"] = ["nonexistent-task"]
    with pytest.raises(PlanningError, match="invariants"):
        plan_with(json.dumps(plan))


def test_self_dependency_rejected() -> None:
    plan = url_shortener_plan()
    plan["tasks"][1]["dependencies"] = ["sqlite-storage"]
    with pytest.raises(PlanningError):
        plan_with(json.dumps(plan))


def test_cycle_rejected() -> None:
    plan = url_shortener_plan()
    plan["tasks"][0]["dependencies"] = ["readme-docs"]
    with pytest.raises(PlanningError, match="invariants"):
        plan_with(json.dumps(plan))


def test_task_count_bound_enforced() -> None:
    plan = {
        "tasks": [
            {
                "task_id": f"task-{index}",
                "description": "work",
                "dependencies": [],
                "required_capabilities": ["testing"],
                "acceptance_criteria": ["done"],
            }
            for index in range(11)
        ]
    }
    with pytest.raises(PlanningError, match="bound"):
        plan_with(json.dumps(plan))


def test_invalid_task_contract_data_rejected() -> None:
    plan = url_shortener_plan()
    plan["tasks"][0]["acceptance_criteria"] = []
    with pytest.raises(PlanningError, match="invalid"):
        plan_with(json.dumps(plan))


def test_missing_capabilities_rejected() -> None:
    plan = url_shortener_plan()
    plan["tasks"][0]["required_capabilities"] = []
    with pytest.raises(PlanningError, match="capability"):
        plan_with(json.dumps(plan))


def test_empty_objective_rejected() -> None:
    with pytest.raises(PlanningError, match="objective"):
        plan_with(json.dumps(url_shortener_plan()), objective="   ")


def test_non_object_and_empty_outputs_rejected() -> None:
    with pytest.raises(PlanningError, match="JSON object"):
        plan_with(json.dumps([1, 2]))
    with pytest.raises(PlanningError, match="tasks"):
        plan_with(json.dumps({"tasks": []}))


def test_description_bound_enforced() -> None:
    plan = url_shortener_plan()
    plan["tasks"][0]["description"] = "x" * 5000
    with pytest.raises(PlanningError, match="length bound"):
        plan_with(json.dumps(plan))


def test_bounds_configurable() -> None:
    config = PlannerConfig(max_tasks=2)
    with pytest.raises(PlanningError, match="2-task bound"):
        Planner(
            FakeInference(json.dumps(url_shortener_plan())), config=config
        ).plan(OBJECTIVE)


def test_inference_failure_becomes_planning_error() -> None:
    from doom_team.inference import InferenceError, InferenceFailure

    class FailingInference:
        def complete(self, messages):
            raise InferenceError(
                InferenceFailure(kind="timeout", detail="request timed out")
            )

    with pytest.raises(PlanningError, match="inference provider failed"):
        Planner(FailingInference()).plan(OBJECTIVE)


def test_oversized_task_id_and_criteria_rejected() -> None:
    plan = url_shortener_plan()
    plan["tasks"][0]["task_id"] = "x" * 200
    with pytest.raises(PlanningError, match="task_id exceeds"):
        plan_with(json.dumps(plan))
    plan = url_shortener_plan()
    plan["tasks"][0]["acceptance_criteria"] = ["y" * 1000]
    with pytest.raises(PlanningError, match="acceptance_criteria entries"):
        plan_with(json.dumps(plan))


def test_missing_dependencies_key_rejected() -> None:
    plan = url_shortener_plan()
    del plan["tasks"][0]["dependencies"]
    with pytest.raises(PlanningError, match="missing the dependencies field"):
        plan_with(json.dumps(plan))
