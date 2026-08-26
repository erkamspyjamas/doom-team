"""Tests for dynamic capability routing and the specialist registry."""

from __future__ import annotations

import json

import pytest

from doom_team.domain import TaskContract
from doom_team.routing import (
    CAPABILITY_VOCABULARY,
    SPECIALIST_REGISTRY,
    RoutingDecision,
    RoutingError,
    route_task,
    specialist_by_id,
)


def _task(task_id: str, capabilities: tuple[str, ...]) -> TaskContract:
    return TaskContract(
        id=task_id,
        objective_id="obj-1",
        instruction="Do the work",
        acceptance_criteria=("done",),
        required_capabilities=capabilities,
    )


def test_backend_capability_selects_backend_specialist() -> None:
    decision = route_task(_task("t", ("backend.python",)))
    assert decision.specialist_id == "python_backend"


def test_sqlite_capability_selects_persistence_specialist() -> None:
    decision = route_task(_task("t", ("database.sqlite", "backend.python")))
    assert decision.specialist_id == "sqlite_persistence"
    decision = route_task(_task("t", ("database.sqlite",)))
    assert decision.specialist_id == "sqlite_persistence"


def test_testing_capability_selects_test_engineer() -> None:
    assert route_task(_task("t", ("testing",))).specialist_id == "test_engineer"


def test_documentation_capability_selects_technical_writer() -> None:
    assert route_task(_task("t", ("documentation",))).specialist_id == "technical_writer"


def test_specialists_expose_distinct_instruction_profiles() -> None:
    profiles = [profile.instruction_profile for profile in SPECIALIST_REGISTRY]
    assert len(set(profiles)) == len(profiles)
    ids = [profile.specialist_id for profile in SPECIALIST_REGISTRY]
    assert len(set(ids)) == len(ids) == 4
    backend = specialist_by_id("python_backend").instruction_profile
    writer = specialist_by_id("technical_writer").instruction_profile
    assert "API" in backend and "README" in writer
    assert "parameterized" in specialist_by_id("sqlite_persistence").instruction_profile
    assert "pytest" in specialist_by_id("test_engineer").instruction_profile


def test_unknown_capability_fails_closed() -> None:
    with pytest.raises(RoutingError, match="unknown capabilities"):
        route_task(_task("t", ("frontend.react",)))


def test_uncoverable_multi_capability_fails_closed() -> None:
    with pytest.raises(RoutingError, match="no registered specialist"):
        route_task(_task("t", ("backend.python", "documentation")))


def test_empty_capabilities_fail_closed_no_generic_fallback() -> None:
    with pytest.raises(RoutingError, match="no required capabilities"):
        route_task(_task("t", ()))


def test_routing_is_deterministic() -> None:
    task = _task("t", ("database.sqlite", "backend.python"))
    first = route_task(task)
    for _ in range(5):
        assert route_task(task) == first


def test_no_generic_dev_specialist_exists() -> None:
    for profile in SPECIALIST_REGISTRY:
        assert profile.specialist_id != "dev"
        assert profile.supported_capabilities
        assert set(profile.supported_capabilities) <= CAPABILITY_VOCABULARY


def test_routing_decision_is_auditable_and_serializable() -> None:
    decision = route_task(_task("audit-me", ("testing",)))
    assert isinstance(decision, RoutingDecision)
    payload = decision.as_dict()
    round_tripped = json.loads(json.dumps(payload))
    assert round_tripped["task_id"] == "audit-me"
    assert round_tripped["specialist_id"] == "test_engineer"
    assert round_tripped["required_capabilities"] == ["testing"]
    assert "supports all required capabilities" in round_tripped["rationale"]


def test_unknown_specialist_lookup_fails_closed() -> None:
    with pytest.raises(RoutingError, match="unknown specialist"):
        specialist_by_id("generic_dev")
