"""Deterministic Supervisor for acceptance decisions.

The Supervisor decides whether a candidate can be accepted based on evidence
freshness, QA/Reviewer verdicts, and objective acceptance. Generic
orchestration completion is never considered; only repository-derived
evidence and independent verdicts matter.
"""

from __future__ import annotations

from collections.abc import Callable

from doom_team.domain import Candidate, Evidence, SupervisorDecision
from doom_team.evidence import evidence_is_fresh
from doom_team.ports import ApprovalPort


class Supervisor:
    """Deterministic acceptance supervisor."""

    def decide(
        self,
        candidate: Candidate,
        evidence: Evidence | None,
        qa_passed: bool,
        reviewer_passed: bool,
        objective_checker: Callable[[Candidate, Evidence], bool],
        approval_port: ApprovalPort | None = None,
    ) -> tuple[SupervisorDecision, str]:
        """Return a deterministic decision and reason.

        Blocks when evidence is missing/stale or when QA/Reviewer/objective
        checks fail. If all checks pass, returns READY_FOR_FINAL_APPROVAL
        (or RELEASE_AUTHORIZED if a positive approval is provided, otherwise
        HUMAN_REQUIRED when approval is requested but denied).
        """
        if evidence is None:
            return SupervisorDecision.BLOCKED, "missing evidence"
        if not evidence_is_fresh(evidence, candidate):
            return SupervisorDecision.BLOCKED, "stale evidence"
        if not qa_passed:
            return SupervisorDecision.BLOCKED, "QA not passed"
        if not reviewer_passed:
            return SupervisorDecision.BLOCKED, "Reviewer not passed"
        if not objective_checker(candidate, evidence):
            return SupervisorDecision.BLOCKED, "objective not met"

        if approval_port is None:
            return SupervisorDecision.READY_FOR_FINAL_APPROVAL, "ready for final approval"
        if approval_port.request_approval(
            "release",
            {"candidate_id": candidate.id, "frontier_sha": candidate.candidate_commit},
        ):
            return SupervisorDecision.RELEASE_AUTHORIZED, "release authorized"
        return SupervisorDecision.HUMAN_REQUIRED, "human approval required"
