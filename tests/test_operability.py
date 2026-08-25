"""Executable contract tests for the Doom Team service operability contract.

These tests pin the semantics defined in ADR-0007 and docs/OPERABILITY.md:
process lifecycle, durable state ownership, human approval boundary, restart
and idempotency, health/readiness separation, and the human-only irreversible
action list.
"""

import datetime
from pathlib import Path
from typing import Any

from doom_team.domain import (
    ApprovalBoundary,
    Attempt,
    Candidate,
    Evidence,
    SupervisorDecision,
)
from doom_team.evidence import DurableEvidenceStore
from doom_team.gas_city_adapter import GasCityAdapter
from doom_team.permissions import Capability, Role, authorize
from doom_team.recovery import Blocker, RecoveryController
from doom_team.supervisor import Supervisor
from doom_team.verdicts import DurableVerdictStore, Verdict
from doom_team.workspace import GitWorkspace


class FakeGasCityClient:
    """Minimal Gas City client for contract tests."""

    def __init__(self) -> None:
        self.runs: dict[str, dict[str, Any]] = {}
        self._next = 1

    def enqueue(self, task: Any, dependencies: tuple[str, ...] = ()) -> str:
        run_id = f"gas_{self._next}"
        self._next += 1
        self.runs[run_id] = {"status": "pending", "dependencies": dependencies}
        return run_id

    def observe(self, run_id: str) -> Any:
        return self.runs[run_id]

    def is_runtime_failure(self, run_id: str) -> bool:
        return self.runs[run_id]["status"] == "failed_runtime"

    def recover(self, run_id: str) -> None:
        if self.runs[run_id]["status"] == "failed_runtime":
            self.runs[run_id]["status"] = "recovered"


class FakeTask:
    def __init__(self, dependencies: tuple[str, ...] = ()) -> None:
        self.dependencies = dependencies


def _candidate_evidence() -> tuple[Candidate, Evidence]:
    attempt = Attempt(id="attempt-1", task_id="task-1")
    candidate = Candidate(
        id="cand-1",
        task_id="task-1",
        attempt=attempt,
        baseline_commit="base",
        candidate_commit="abc",
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
        artifacts=(),
        actual_model="deepseek-v4",
        actual_runtime="local",
        timestamp=datetime.datetime.now(datetime.timezone.utc),
        digest="sha256:abc",
    )
    return candidate, evidence


def _always_pass(candidate: Candidate, evidence: Evidence) -> bool:
    return True


# --- Human approval boundary ---


def test_approval_boundary_fail_closed() -> None:
    assert ApprovalBoundary().request("push") is False

    class DenyingPort:
        def request_approval(self, action: str, context: dict[str, Any]) -> bool:
            return False

    assert ApprovalBoundary(approval_port=DenyingPort()).request("push") is False

    class ApprovingPort:
        def request_approval(self, action: str, context: dict[str, Any]) -> bool:
            return True

    assert ApprovalBoundary(approval_port=ApprovingPort()).request("push") is True


# --- Irreversible actions are human-only ---


def test_dangerous_actions_require_approval_for_all_roles() -> None:
    for role in Role:
        for capability in (
            Capability.PUSH,
            Capability.MERGE,
            Capability.DEPLOY,
            Capability.DESTRUCTIVE_DB,
        ):
            decision = authorize(role, capability)
            assert decision.decision.value == "REQUIRES_APPROVAL"


def test_no_autonomous_credential_provisioning_capability() -> None:
    # Credential provisioning must never be an autonomous capability.
    capability_names = {c.name for c in Capability}
    assert "CREDENTIAL_PROVISIONING" not in capability_names


def test_supervisor_release_requires_human_approval() -> None:
    candidate, evidence = _candidate_evidence()
    supervisor = Supervisor()

    decision, _ = supervisor.decide(candidate, evidence, True, True, _always_pass)
    assert decision is SupervisorDecision.READY_FOR_FINAL_APPROVAL

    class ApprovingPort:
        def request_approval(self, action: str, context: dict[str, Any]) -> bool:
            return True

    decision, _ = supervisor.decide(
        candidate, evidence, True, True, _always_pass, approval_port=ApprovingPort()
    )
    assert decision is SupervisorDecision.RELEASE_AUTHORIZED

    class DenyingPort:
        def request_approval(self, action: str, context: dict[str, Any]) -> bool:
            return False

    decision, _ = supervisor.decide(
        candidate, evidence, True, True, _always_pass, approval_port=DenyingPort()
    )
    assert decision is SupervisorDecision.HUMAN_REQUIRED


# --- Restart semantics and idempotency ---


def test_restart_survives_semantic_exhaustion(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    c1 = RecoveryController(path)
    epoch = c1.initialize("task-1", semantic_retries=1)
    c1.on_semantic_blocker("task-1", epoch.attempt, Blocker("qa", "failed"))
    assert c1.is_exhausted("task-1")

    c2 = RecoveryController(path)
    assert c2.is_exhausted("task-1")
    assert c2.next_attempt("task-1") is None


def test_restart_survives_orchestration_mapping(tmp_path: Path) -> None:
    store = tmp_path / "store.json"
    client = FakeGasCityClient()
    a1 = GasCityAdapter(client, store)
    run_id = a1.enqueue(FakeTask())
    a2 = GasCityAdapter(client, store)
    observed = a2.observe(run_id)
    assert observed.run_id == run_id


def test_restart_does_not_double_consume(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    c1 = RecoveryController(path)
    epoch = c1.initialize("task-1", semantic_retries=2)
    attempt = epoch.attempt
    c1.on_semantic_blocker("task-1", attempt, Blocker("qa", "failed"))
    c1.on_semantic_blocker("task-1", attempt, Blocker("qa", "failed again"))
    state = c1.state("task-1")
    assert state is not None
    assert state.semantic_retries_left == 1

    c2 = RecoveryController(path)
    state2 = c2.state("task-1")
    assert state2 is not None
    assert state2.semantic_retries_left == 1


def test_runtime_retry_does_not_consume_semantic_budget(tmp_path: Path) -> None:
    controller = RecoveryController(tmp_path / "state.json")
    epoch = controller.initialize("task-1", semantic_retries=2)
    controller.record_runtime_retry(epoch.attempt)
    state = controller.state("task-1")
    assert state is not None
    assert state.semantic_retries_left == 2
    assert not controller.is_exhausted("task-1")


def test_durable_state_reopens_idempotently(tmp_path: Path) -> None:
    ev_store = DurableEvidenceStore(tmp_path / "store")
    ev = {
        "id": "ev-1",
        "candidate_id": "cand-1",
        "candidate_commit": "abc",
        "objective_id": "obj-1",
        "mission_id": "mission-1",
        "producer_role": "dev",
        "producer_run": "run-1",
        "command": "make test",
        "artifacts": [],
        "actual_model": "deepseek-v4",
        "actual_runtime": "local",
        "timestamp": "2024-01-01T00:00:00+00:00",
        "digest": "sha256:abc",
    }
    saved = ev_store.save(ev)
    ev_store2 = DurableEvidenceStore(tmp_path / "store")
    loaded = ev_store2.load(saved)
    assert loaded["id"] == "ev-1"

    verdict_store = DurableVerdictStore(tmp_path / "verdicts")
    verdict = Verdict(candidate_id="cand-1", role="QA", decision=True)
    vid = verdict_store.save(verdict)
    verdict_store2 = DurableVerdictStore(tmp_path / "verdicts")
    loaded_verdict = verdict_store2.load(vid)
    assert loaded_verdict.candidate_id == "cand-1"


def test_evidence_save_idempotent_by_id(tmp_path: Path) -> None:
    store = DurableEvidenceStore(tmp_path / "store")
    ev = {
        "id": "ev-1",
        "candidate_id": "cand-1",
        "candidate_commit": "abc",
        "objective_id": "obj-1",
        "mission_id": "mission-1",
        "producer_role": "dev",
        "producer_run": "run-1",
        "command": "make test",
        "artifacts": [],
        "actual_model": "deepseek-v4",
        "actual_runtime": "local",
        "timestamp": "2024-01-01T00:00:00+00:00",
        "digest": "sha256:abc",
    }
    id1 = store.save(ev)
    id2 = store.save(ev)
    assert id1 == id2
    evidence_files = list((tmp_path / "store" / "evidence").glob("*.json"))
    assert len(evidence_files) == 1


# --- Health/readiness separation and orchestration ---


def test_orchestration_completion_does_not_imply_acceptance(tmp_path: Path) -> None:
    client = FakeGasCityClient()
    adapter = GasCityAdapter(client, tmp_path / "store.json")
    run_id = adapter.enqueue(FakeTask())
    gas_id = adapter._mapping[run_id]
    client.runs[gas_id]["status"] = "completed"
    observed = adapter.observe(run_id)
    assert observed.status == "completed"
    # No acceptance state leaks from orchestration.
    assert not hasattr(observed, "acceptance")


def test_workspace_exposes_no_release_mutation(tmp_path: Path) -> None:
    ws = GitWorkspace(tmp_path / "workspaces")
    for method in ("push", "merge", "deploy", "clean"):
        assert not hasattr(ws, method)
