"""Core domain model for Doom Team.

These dataclasses capture the essential entities and their invariants
without depending on any infrastructure or orchestration engine.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol, runtime_checkable


class AcceptanceState(Enum):
    UNASSESSED = "UNASSESSED"
    CANDIDATE_READY = "CANDIDATE_READY"
    QA_ACCEPTED = "QA_ACCEPTED"
    REVIEW_ACCEPTED = "REVIEW_ACCEPTED"
    OBJECTIVE_ACCEPTED = "OBJECTIVE_ACCEPTED"
    WAITING_FOR_FINAL_APPROVAL = "WAITING_FOR_FINAL_APPROVAL"
    RELEASE_AUTHORIZED = "RELEASE_AUTHORIZED"


@dataclass(frozen=True)
class Objective:
    id: str
    description: str


@dataclass(frozen=True)
class TaskContract:
    id: str
    objective_id: str
    instruction: str
    acceptance_criteria: tuple[str, ...]
    dependencies: tuple[str, ...] = ()


@dataclass(frozen=True)
class Mission:
    id: str
    objective: Objective
    tasks: tuple[TaskContract, ...]


@dataclass(frozen=True)
class Attempt:
    id: str
    task_id: str
    recovery_epoch: int = 0


@dataclass(frozen=True)
class RecoveryEpoch:
    attempt: Attempt
    semantic_retries_left: int


@dataclass(frozen=True)
class Candidate:
    id: str
    task_id: str
    attempt: Attempt
    baseline_commit: str
    candidate_commit: str
    workspace_id: str


@dataclass(frozen=True)
class Evidence:
    id: str
    candidate: Candidate
    producer_role: str
    producer_run: str
    command: str
    artifacts: tuple[str, ...]
    actual_model: str
    actual_runtime: str
    timestamp: datetime.datetime
    digest: str


@dataclass(frozen=True)
class Acceptance:
    candidate: Candidate
    state: AcceptanceState = AcceptanceState.UNASSESSED


@runtime_checkable
class ApprovalPort(Protocol):
    def request_approval(self, action: str, context: dict[str, Any]) -> bool:
        """Request human approval for an irreversible action."""


@dataclass
class ApprovalBoundary:
    """Gate irreversible actions behind explicit human approval.

    The autonomous path must never call a mutation without first obtaining
    a positive approval through this boundary.
    """

    approval_port: ApprovalPort | None = None

    def request(self, action: str, context: dict[str, Any] | None = None) -> bool:
        """Return True only if a positive human approval is recorded."""
        if self.approval_port is None:
            return False
        return self.approval_port.request_approval(action, context or {})
