"""Tests for the deterministic Supervisor acceptance decisions."""

import datetime
from typing import Any

from doom_team.domain import Attempt, Candidate, Evidence, SupervisorDecision
from doom_team.supervisor import Supervisor


def _make_candidate(candidate_id: str = "cand-1", commit: str = "abc") -> Candidate:
    attempt = Attempt(id="attempt-1", task_id="task-1")
    return Candidate(
        id=candidate_id,
        task_id="task-1",
        attempt=attempt,
        baseline_commit="base",
        candidate_commit=commit,
        workspace_id="ws-1",
    )


def _make_evidence(candidate: Candidate) -> Evidence:
    return Evidence(
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


def _always_pass(candidate: Candidate, evidence: Evidence) -> bool:
    return True


def _always_fail(candidate: Candidate, evidence: Evidence) -> bool:
    return False


def test_missing_evidence_blocks() -> None:
    candidate = _make_candidate()
    supervisor = Supervisor()
    decision, reason = supervisor.decide(candidate, None, True, True, _always_pass)
    assert decision is SupervisorDecision.BLOCKED
    assert "missing" in reason


def test_stale_evidence_blocks() -> None:
    candidate = _make_candidate(commit="abc")
    other = _make_candidate(candidate_id="cand-2", commit="def")
    evidence = _make_evidence(other)
    supervisor = Supervisor()
    decision, reason = supervisor.decide(candidate, evidence, True, True, _always_pass)
    assert decision is SupervisorDecision.BLOCKED
    assert "stale" in reason


def test_qa_failure_blocks() -> None:
    candidate = _make_candidate()
    evidence = _make_evidence(candidate)
    supervisor = Supervisor()
    decision, reason = supervisor.decide(candidate, evidence, False, True, _always_pass)
    assert decision is SupervisorDecision.BLOCKED
    assert "QA" in reason


def test_reviewer_failure_blocks() -> None:
    candidate = _make_candidate()
    evidence = _make_evidence(candidate)
    supervisor = Supervisor()
    decision, reason = supervisor.decide(candidate, evidence, True, False, _always_pass)
    assert decision is SupervisorDecision.BLOCKED
    assert "Reviewer" in reason


def test_objective_failure_blocks() -> None:
    candidate = _make_candidate()
    evidence = _make_evidence(candidate)
    supervisor = Supervisor()
    decision, reason = supervisor.decide(candidate, evidence, True, True, _always_fail)
    assert decision is SupervisorDecision.BLOCKED
    assert "objective" in reason


def test_ready_for_final_approval_when_all_pass() -> None:
    candidate = _make_candidate()
    evidence = _make_evidence(candidate)
    supervisor = Supervisor()
    decision, reason = supervisor.decide(candidate, evidence, True, True, _always_pass)
    assert decision is SupervisorDecision.READY_FOR_FINAL_APPROVAL
    assert "ready" in reason


def test_release_authorized_with_approval() -> None:
    class ApprovingPort:
        def request_approval(self, action: str, context: dict[str, Any]) -> bool:
            return True

    candidate = _make_candidate()
    evidence = _make_evidence(candidate)
    supervisor = Supervisor()
    decision, reason = supervisor.decide(
        candidate, evidence, True, True, _always_pass, approval_port=ApprovingPort()
    )
    assert decision is SupervisorDecision.RELEASE_AUTHORIZED
    assert "release" in reason


def test_human_required_when_approval_denied() -> None:
    class DenyingPort:
        def request_approval(self, action: str, context: dict[str, Any]) -> bool:
            return False

    candidate = _make_candidate()
    evidence = _make_evidence(candidate)
    supervisor = Supervisor()
    decision, reason = supervisor.decide(
        candidate, evidence, True, True, _always_pass, approval_port=DenyingPort()
    )
    assert decision is SupervisorDecision.HUMAN_REQUIRED
    assert "human" in reason
