"""Tests for the core domain model."""

import datetime
from typing import Any

from doom_team.domain import (
    AcceptanceState,
    ApprovalBoundary,
    Attempt,
    Candidate,
    Evidence,
    Objective,
    RecoveryEpoch,
    TaskContract,
)


def test_acceptance_state_order() -> None:
    states = list(AcceptanceState)
    assert states == [
        AcceptanceState.UNASSESSED,
        AcceptanceState.CANDIDATE_READY,
        AcceptanceState.QA_ACCEPTED,
        AcceptanceState.REVIEW_ACCEPTED,
        AcceptanceState.OBJECTIVE_ACCEPTED,
        AcceptanceState.WAITING_FOR_FINAL_APPROVAL,
        AcceptanceState.RELEASE_AUTHORIZED,
    ]


def test_approval_boundary_denies_without_port() -> None:
    boundary = ApprovalBoundary()
    assert boundary.request("push") is False


def test_approval_boundary_forwards_to_port() -> None:
    class StubApproval:
        def request_approval(self, action: str, context: dict[str, Any]) -> bool:
            return True

    boundary = ApprovalBoundary(approval_port=StubApproval())
    assert boundary.request("push", {"branch": "main"}) is True


def test_evidence_has_required_fields() -> None:
    objective = Objective(id="obj-1", description="Build foundation")
    task = TaskContract(
        id="task-1",
        objective_id="obj-1",
        instruction="Bootstrap",
        acceptance_criteria=("c1",),
    )
    attempt = Attempt(id="attempt-1", task_id="task-1")
    candidate = Candidate(
        id="cand-1",
        task_id="task-1",
        attempt=attempt,
        baseline_commit="abc",
        candidate_commit="def",
        workspace_id="ws-1",
    )
    evidence = Evidence(
        id="ev-1",
        candidate=candidate,
        objective_id="obj-1",
        mission_id="mission-1",
        producer_role="dev",
        producer_run="run-1",
        command="make test",
        artifacts=("log.txt",),
        actual_model="deepseek-v4",
        actual_runtime="local",
        timestamp=datetime.datetime.now(datetime.timezone.utc),
        digest="sha256:deadbeef",
    )
    assert evidence.candidate is candidate
    assert evidence.candidate.attempt.recovery_epoch == 0
    assert evidence.objective_id == "obj-1"
    assert evidence.mission_id == "mission-1"


def test_recovery_epoch_holds_budget() -> None:
    attempt = Attempt(id="a1", task_id="t1")
    epoch = RecoveryEpoch(attempt=attempt, semantic_retries_left=2)
    assert epoch.semantic_retries_left == 2
