"""Tests for evidence stores, durability, freshness, and telemetry."""

import datetime
from pathlib import Path

from doom_team.domain import Attempt, Candidate, Evidence, Objective, TaskContract
from doom_team.evidence import DurableEvidenceStore, evidence_is_fresh
from doom_team.telemetry import InMemoryTelemetry


OBJECTIVE = Objective(id="obj-1", description="Build foundation")
TASK = TaskContract(
    id="task-1",
    objective_id="obj-1",
    instruction="Bootstrap",
    acceptance_criteria=("c1",),
)


def _make_evidence(
    candidate_id: str = "cand-1",
    candidate_commit: str = "def",
    artifact: str | None = None,
) -> Evidence:
    attempt = Attempt(id="attempt-1", task_id="task-1")
    candidate = Candidate(
        id=candidate_id,
        task_id="task-1",
        attempt=attempt,
        baseline_commit="abc",
        candidate_commit=candidate_commit,
        workspace_id="ws-1",
    )
    artifacts = (artifact,) if artifact else ()
    return Evidence(
        id="ev-1",
        candidate=candidate,
        objective_id="obj-1",
        mission_id="mission-1",
        producer_role="dev",
        producer_run="run-1",
        command="make test",
        artifacts=artifacts,
        actual_model="deepseek-v4",
        actual_runtime="local",
        timestamp=datetime.datetime.now(datetime.timezone.utc),
        digest="sha256:deadbeef",
    )


def _to_dict(evidence: Evidence) -> dict:
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


def test_durable_store_persists_evidence(tmp_path: Path) -> None:
    store = DurableEvidenceStore(tmp_path)
    evidence = _make_evidence()
    saved_id = store.save(_to_dict(evidence))
    loaded = store.load(saved_id)
    assert loaded["id"] == evidence.id
    assert loaded["candidate_id"] == evidence.candidate.id
    assert loaded["actual_model"] == "deepseek-v4"
    assert loaded["actual_runtime"] == "local"


def test_artifacts_copied_into_store(tmp_path: Path) -> None:
    store = DurableEvidenceStore(tmp_path)
    artifact = tmp_path / "log.txt"
    artifact.write_text("logs")
    evidence = _make_evidence(artifact=str(artifact))
    saved_id = store.save(_to_dict(evidence))
    loaded = store.load(saved_id)
    assert len(loaded["artifacts"]) == 1
    stored_artifact = Path(loaded["artifacts"][0])
    assert stored_artifact.is_file()
    assert stored_artifact.read_text() == "logs"


def test_freshness_rejects_stale_evidence() -> None:
    evidence1 = _make_evidence(candidate_id="cand-1", candidate_commit="commit1")
    evidence2 = _make_evidence(candidate_id="cand-2", candidate_commit="commit2")
    assert evidence_is_fresh(evidence1, evidence1.candidate)
    assert not evidence_is_fresh(evidence1, evidence2.candidate)


def test_telemetry_records_actual_route() -> None:
    telemetry = InMemoryTelemetry()
    telemetry.record("endpoint", "deepseek-v4", "local")
    assert telemetry.last() == {"endpoint": "endpoint", "model": "deepseek-v4", "runtime": "local"}
