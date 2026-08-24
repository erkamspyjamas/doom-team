"""DEV to QA to Reviewer lifecycle orchestrator."""

from __future__ import annotations

import datetime
import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from doom_team.domain import Attempt, Candidate, Evidence, TaskContract
from doom_team.ports import EvidenceStore, RepositoryPort, WorkspacePort
from doom_team.qa_reviewer import Verdict, VerdictStore, run_validation


class DevAgent(Protocol):
    """A DEV implementation that mutates a workspace to produce a candidate."""

    def __call__(
        self,
        task: TaskContract,
        workspace: WorkspacePort,
        repository: RepositoryPort,
        workspace_id: str,
    ) -> None:
        ...


@dataclass(frozen=True)
class LifecycleResult:
    candidate: Candidate
    evidence: Evidence
    qa_verdict: Verdict
    reviewer_verdict: Verdict


def _evidence_to_dict(evidence: Evidence) -> dict[str, Any]:
    return {
        "id": evidence.id,
        "candidate_id": evidence.candidate.id,
        "candidate_commit": evidence.candidate.candidate_commit,
        "objective_id": evidence.objective_id,
        "mission_id": evidence.mission_id,
        "producer_role": evidence.producer_role,
        "producer_run": evidence.producer_run,
        "command": evidence.command,
        "artifacts": list(evidence.artifacts),
        "actual_model": evidence.actual_model,
        "actual_runtime": evidence.actual_runtime,
        "timestamp": evidence.timestamp.isoformat(),
        "digest": evidence.digest,
    }


def _evidence_digest(candidate: Candidate, evidence_fields: dict[str, Any]) -> str:
    """Compute a candidate-bound SHA-256 digest from candidate and evidence fields."""
    payload = {
        "candidate_id": candidate.id,
        "candidate_commit": candidate.candidate_commit,
        **evidence_fields,
    }
    raw = json.dumps(payload, sort_keys=True).encode()
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


def run_candidate_lifecycle(
    task: TaskContract,
    mission_id: str,
    workspace: WorkspacePort,
    repository: RepositoryPort,
    evidence_store: EvidenceStore,
    verdict_store: VerdictStore,
    dev_agent: DevAgent,
    producer_run: str,
    actual_model: str,
    actual_runtime: str,
    qa_verify: Callable[[Candidate, Evidence], tuple[Any, Any]],
    reviewer_verify: Callable[[Candidate, Evidence], tuple[Any, Any]],
) -> LifecycleResult:
    """Run a full DEV -> QA -> Reviewer lifecycle for a task."""
    ws_id = workspace.create_workspace(task.id)
    baseline = repository.baseline_commit(ws_id)
    dev_agent(task, workspace, repository, ws_id)
    candidate_commit = repository.candidate_commit(ws_id)

    attempt = Attempt(
        id=f"att_{uuid.uuid4().hex[:8]}",
        task_id=task.id,
        recovery_epoch=0,
    )
    candidate = Candidate(
        id=f"cand_{uuid.uuid4().hex[:8]}",
        task_id=task.id,
        attempt=attempt,
        baseline_commit=baseline,
        candidate_commit=candidate_commit,
        workspace_id=ws_id,
    )

    evidence_fields = {
        "objective_id": task.objective_id,
        "mission_id": mission_id,
        "producer_role": "dev",
        "producer_run": producer_run,
        "command": "pytest",
        "artifacts": (),
        "actual_model": actual_model,
        "actual_runtime": actual_runtime,
    }
    digest = _evidence_digest(candidate, evidence_fields)
    evidence = Evidence(
        id=f"ev_{uuid.uuid4().hex[:8]}",
        candidate=candidate,
        **evidence_fields,
        timestamp=datetime.datetime.now(datetime.timezone.utc),
        digest=digest,
    )
    evidence_store.save(_evidence_to_dict(evidence))

    qa_verdict = run_validation(
        candidate,
        evidence,
        "qa",
        f"{producer_run}-qa",
        verdict_store,
        qa_verify,
    )
    reviewer_verdict = run_validation(
        candidate,
        evidence,
        "reviewer",
        f"{producer_run}-reviewer",
        verdict_store,
        reviewer_verify,
    )
    return LifecycleResult(candidate, evidence, qa_verdict, reviewer_verdict)
