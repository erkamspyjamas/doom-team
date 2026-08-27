"""Tests for the happy-path mission orchestrator, from objective to approval gate."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from doom_team.approval import DurableApprovalPort
from doom_team.deliverable import DeliverableRepo, _git
from doom_team.mission import (
    MISSION_APPROVED,
    MISSION_AWAITING_HUMAN_APPROVAL,
    MISSION_BLOCKED,
    MISSION_FINAL_VALIDATION_FAILED,
    MISSION_REJECTED,
    TASK_INTEGRATED,
    MissionLedgerError,
    MissionStore,
    approve_mission,
)
from doom_team.mission_runner import MissionRunError, MissionRunner
from doom_team.validation import ValidationCommand

PY = sys.executable

VALIDATION = (
    ValidationCommand(label="compile", argv=(PY, "-m", "compileall", "-q", ".")),
    ValidationCommand(label="tests", argv=(PY, "-m", "pytest", "-q")),
)
# A stricter final gate than any single task runs: it also demands packaging
# metadata that no task in the fixture mission produces.
STRICT_FINAL = VALIDATION + (
    ValidationCommand(
        label="package-check",
        argv=(
            PY,
            "-c",
            "import pathlib,sys; sys.exit(0 if pathlib.Path('pyproject.toml').exists() else 1)",
        ),
    ),
)

PASS_VERDICT = json.dumps({"verdict": "pass", "summary": "looks correct", "blockers": []})
BLOCK_VERDICT = json.dumps(
    {"verdict": "block", "summary": "defect found", "blockers": ["missing edge case"]}
)

PLAN = json.dumps(
    {
        "tasks": [
            {
                "task_id": "arith-code",
                "description": "Implement the arithmetic module",
                "dependencies": [],
                "required_capabilities": ["backend.python"],
                "acceptance_criteria": ["add and multiply behave correctly"],
            },
            {
                "task_id": "arith-tests",
                "description": "Add edge-case tests for the module",
                "dependencies": ["arith-code"],
                "required_capabilities": ["testing"],
                "acceptance_criteria": ["edge cases are covered by tests"],
            },
            {
                "task_id": "arith-docs",
                "description": "Write the README for the package",
                "dependencies": ["arith-tests"],
                "required_capabilities": ["documentation"],
                "acceptance_criteria": ["README documents install and usage"],
            },
        ]
    }
)

ARITH = "def add(a, b):\n    return a + b\n\n\ndef multiply(a, b):\n    return a * b\n"
GOOD_WORK = {
    "Implement the arithmetic module": {
        "arith.py": ARITH,
        "test_arith.py": (
            "from arith import add, multiply\n\n\n"
            "def test_add():\n    assert add(2, 3) == 5\n\n\n"
            "def test_multiply():\n    assert multiply(2, 3) == 6\n"
        ),
    },
    "Add edge-case tests": {
        "test_arith_edge.py": (
            "from arith import add, multiply\n\n\n"
            "def test_add_negative():\n    assert add(-1, -1) == -2\n\n\n"
            "def test_multiply_zero():\n    assert multiply(5, 0) == 0\n"
        )
    },
    "Write the README": {
        "README.md": "# Arithmetic\n\nInstall: none needed.\n\nRun: python -m pytest\n"
    },
}


class _Response:
    def __init__(self, content: str) -> None:
        self.content = content


def _implementer_json(files: dict[str, str]) -> str:
    return json.dumps(
        {
            "files": [{"path": p, "content": c} for p, c in files.items()],
            "summary": "implement the task",
        }
    )


class MissionInference:
    """One deterministic fake standing in for the real provider, per role."""

    def __init__(
        self,
        *,
        plan: str = PLAN,
        work: dict[str, dict[str, str]] | None = None,
        raw_work: dict[str, str] | None = None,
        qa: str | Callable[[str], str] = PASS_VERDICT,
        reviewer: str | Callable[[str], str] = PASS_VERDICT,
        on_implementer: Callable[[str], None] | None = None,
    ) -> None:
        self.plan = plan
        self.work = GOOD_WORK if work is None else work
        self.raw_work = raw_work or {}
        self.qa = qa
        self.reviewer = reviewer
        self.on_implementer = on_implementer
        self.implemented: list[str] = []
        self.systems: list[str] = []

    @staticmethod
    def _pick(value: str | Callable[[str], str], user: str) -> str:
        return value(user) if callable(value) else value

    def complete(self, messages: list[dict[str, str]]) -> _Response:
        system = messages[0]["content"]
        user = messages[1]["content"]
        if "mission planner" in system:
            return _Response(self.plan)
        if "implementation specialist for exactly one" in system:
            self.systems.append(system)
            if self.on_implementer is not None:
                self.on_implementer(user)
            for marker, raw in self.raw_work.items():
                if marker in user:
                    self.implemented.append(marker)
                    return _Response(raw)
            for marker, files in self.work.items():
                if marker in user:
                    self.implemented.append(marker)
                    return _Response(_implementer_json(files))
            raise AssertionError(f"no implementation scripted for: {user[:300]}")
        if "independent QA engineer" in system:
            return _Response(self._pick(self.qa, user))
        if "independent final reviewer" in system:
            return _Response(self._pick(self.reviewer, user))
        raise AssertionError(f"unexpected role prompt: {system[:120]}")


def _runner(
    tmp_path: Path,
    inference: MissionInference,
    *,
    final: tuple[ValidationCommand, ...] | None = None,
) -> tuple[MissionRunner, MissionStore]:
    store = MissionStore(tmp_path / "state")
    runner = MissionRunner(
        inference,
        store,
        workspaces_root=tmp_path / "work",
        validation_commands=VALIDATION,
        final_validation_commands=final,
    )
    return runner, store


def _run(
    tmp_path: Path,
    inference: MissionInference | None = None,
    *,
    final: tuple[ValidationCommand, ...] | None = None,
) -> tuple[Any, MissionStore, MissionInference]:
    inference = inference or MissionInference()
    runner, store = _runner(tmp_path, inference, final=final)
    result = runner.run("Create a tiny Python arithmetic package", tmp_path / "project")
    return result, store, inference


# --- 31: the whole happy path through ONE MissionRunner invocation ---------


def test_full_happy_path_single_invocation(tmp_path: Path) -> None:
    result, store, inference = _run(tmp_path)

    assert result.status == MISSION_AWAITING_HUMAN_APPROVAL
    assert result.awaiting_approval and result.approval_required
    record = result.record

    # Every planned task ran, was accepted, and was integrated.
    assert [t.task_id for t in record.tasks] == ["arith-code", "arith-docs", "arith-tests"]
    assert all(t.state == TASK_INTEGRATED for t in record.tasks)

    # Routing was automatic and produced three DIFFERENT specialists.
    routed = {item["task_id"]: item["specialist_id"] for item in record.routing}
    assert routed == {
        "arith-code": "python_backend",
        "arith-tests": "test_engineer",
        "arith-docs": "technical_writer",
    }
    assert len(set(routed.values())) == 3

    # The frontier advanced once per task and ends where the mission ends.
    integrated = [record.task(t).integrated_sha for t in ("arith-code", "arith-tests", "arith-docs")]
    assert len(set(integrated)) == 3
    assert record.frontier_sha == integrated[-1] == record.final_frontier_sha

    # Task N+1 started from exactly the frontier task N produced.
    assert record.task("arith-tests").baseline_sha == record.task("arith-code").integrated_sha
    assert record.task("arith-docs").baseline_sha == record.task("arith-tests").integrated_sha
    assert record.task("arith-code").baseline_sha == record.baseline_sha

    # Fresh final validation really ran and really passed.
    assert record.final_validation is not None
    assert [i["label"] for i in record.final_validation] == ["compile", "tests"]
    assert record.final_validation_passed

    # The deliverable holds the accumulated work; nothing was pushed anywhere.
    repo = DeliverableRepo.open(tmp_path / "project")
    assert repo.head() == record.final_frontier_sha
    assert repo.remotes() == ()
    for name in ("arith.py", "test_arith.py", "test_arith_edge.py", "README.md"):
        assert (repo.path / name).exists(), name

    # Not approved: a human decision is still required.
    assert record.approval is None
    assert store.load(record.mission_id).status == MISSION_AWAITING_HUMAN_APPROVAL


def test_dependencies_order_execution_not_the_alphabet(tmp_path: Path) -> None:
    """arith-docs sorts before arith-tests, but depends on it transitively."""
    _, _, inference = _run(tmp_path)
    assert inference.implemented == [
        "Implement the arithmetic module",
        "Add edge-case tests",
        "Write the README",
    ]


def test_routed_specialist_reaches_the_real_task_runner(tmp_path: Path) -> None:
    _, _, inference = _run(tmp_path)
    assert len(inference.systems) == 3
    assert "backend implementation specialist" in inference.systems[0]
    assert "test engineering specialist" in inference.systems[1]
    assert "documentation specialist" in inference.systems[2]
    assert len(set(inference.systems)) == 3


# --- 32-35: blocked paths. No retries, no integration of failed work -------


def _assert_stopped_after_first_task(record: Any, expected_state: str) -> None:
    assert record.status == MISSION_BLOCKED
    assert record.task("arith-code").state == expected_state
    assert record.task("arith-code").integrated_sha == ""
    # Dependent tasks never started.
    assert record.task("arith-tests").state == "pending"
    assert record.task("arith-docs").state == "pending"
    # The frontier never moved off the baseline.
    assert record.frontier_sha == record.baseline_sha
    assert record.final_validation is None


def test_task_validation_failure_blocks_the_mission(tmp_path: Path) -> None:
    broken = dict(GOOD_WORK)
    broken["Implement the arithmetic module"] = {
        "arith.py": "def add(a, b):\n    return a - b\n",  # wrong on purpose
        "test_arith.py": "from arith import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n",
    }
    result, store, inference = _run(tmp_path, MissionInference(work=broken))
    _assert_stopped_after_first_task(result.record, "validation_failed")
    assert result.record.blocker is not None
    assert result.record.blocker["reason"] == "validation_failed"
    assert result.record.blocker["task_id"] == "arith-code"
    # Real validation evidence is preserved for the operator.
    validation = result.record.task("arith-code").validation
    assert any(item["passed"] is False for item in validation)
    # Only one task was ever implemented: no retry, no dependent work.
    assert inference.implemented == ["Implement the arithmetic module"]
    assert DeliverableRepo.open(tmp_path / "project").head() == result.record.baseline_sha


def test_qa_block_blocks_the_mission(tmp_path: Path) -> None:
    result, _, inference = _run(tmp_path, MissionInference(qa=BLOCK_VERDICT))
    _assert_stopped_after_first_task(result.record, "qa_blocked")
    qa = result.record.task("arith-code").qa
    assert qa is not None and qa["verdict"] == "block"
    assert result.record.task("arith-code").reviewer is None
    assert inference.implemented == ["Implement the arithmetic module"]


def test_reviewer_block_blocks_the_mission(tmp_path: Path) -> None:
    result, _, inference = _run(tmp_path, MissionInference(reviewer=BLOCK_VERDICT))
    _assert_stopped_after_first_task(result.record, "reviewer_blocked")
    task = result.record.task("arith-code")
    assert task.qa is not None and task.qa["verdict"] == "pass"
    assert task.reviewer is not None and task.reviewer["verdict"] == "block"
    assert inference.implemented == ["Implement the arithmetic module"]


def test_execution_failure_stops_the_mission_cleanly(tmp_path: Path) -> None:
    result, _, inference = _run(
        tmp_path,
        MissionInference(raw_work={"Implement the arithmetic module": "sure, here is the code!"}),
    )
    _assert_stopped_after_first_task(result.record, "execution_failed")
    assert "not valid JSON" in result.record.task("arith-code").failure_detail
    assert result.record.task("arith-code").candidate_sha == ""


def test_final_validation_failure_never_reaches_the_approval_gate(tmp_path: Path) -> None:
    result, store, _ = _run(tmp_path, final=STRICT_FINAL)
    assert result.status == MISSION_FINAL_VALIDATION_FAILED
    assert not result.awaiting_approval and not result.approval_required
    record = result.record
    # All the work really was done and integrated first.
    assert all(t.state == TASK_INTEGRATED for t in record.tasks)
    assert record.final_validation is not None
    labels = {i["label"]: i["passed"] for i in record.final_validation}
    assert labels["compile"] is True and labels["tests"] is True
    assert labels["package-check"] is False
    assert record.final_frontier_sha == ""  # never entered the gate
    assert store.load(record.mission_id).status == MISSION_FINAL_VALIDATION_FAILED


def test_non_fast_forward_integration_is_a_typed_block(tmp_path: Path) -> None:
    """A deliverable that moved under the mission stops it; nothing is forced."""

    def advance_deliverable(_user: str) -> None:
        repo_path = tmp_path / "project"
        if (repo_path / "outside.txt").exists():
            return
        (repo_path / "outside.txt").write_text("a concurrent writer\n")
        _git(repo_path, "add", "-A")
        _git(repo_path, "commit", "-q", "-m", "out-of-band commit")

    result, _, _ = _run(tmp_path, MissionInference(on_implementer=advance_deliverable))
    record = result.record
    assert record.status == MISSION_BLOCKED
    assert record.blocker is not None
    assert record.blocker["reason"] == "integration_failed"
    assert "fast-forward" in record.blocker["detail"]
    # The task itself was accepted, but its candidate never entered the frontier.
    assert record.task("arith-code").state == "accepted"
    assert record.task("arith-code").integrated_sha == ""
    assert record.task("arith-tests").state == "pending"


def test_planning_failure_creates_no_mission(tmp_path: Path) -> None:
    runner, store = _runner(tmp_path, MissionInference(plan="not json at all"))
    with pytest.raises(MissionRunError, match="planning failed"):
        runner.run("Create something", tmp_path / "project")
    assert store.list_ids() == ()
    assert not (tmp_path / "project").exists()


# --- 12: durability through a brand-new reader -----------------------------


def test_completed_mission_is_visible_to_a_new_reader(tmp_path: Path) -> None:
    result, _, _ = _run(tmp_path)
    mission_id = result.mission_id

    fresh = MissionStore(tmp_path / "state")  # new instance, nothing in memory
    record = fresh.load(mission_id)
    assert record.objective == "Create a tiny Python arithmetic package"
    assert len(record.tasks) == 3
    assert len(record.routing) == 3
    assert record.status == MISSION_AWAITING_HUMAN_APPROVAL
    assert record.final_frontier_sha == result.record.final_frontier_sha
    assert record.deliverable_path == str((tmp_path / "project").resolve())
    for task in record.tasks:
        assert task.specialist_id
        assert task.baseline_sha and task.candidate_sha and task.integrated_sha
        assert task.validation and all(item["passed"] for item in task.validation)
        assert task.qa is not None and task.qa["verdict"] == "pass"
        assert task.reviewer is not None and task.reviewer["verdict"] == "pass"
    assert record.final_validation is not None and record.final_validation_passed
    assert mission_id in fresh.list_ids()


# --- 21-25: the human approval gate ----------------------------------------


def _port(tmp_path: Path) -> DurableApprovalPort:
    return DurableApprovalPort(tmp_path / "state")


def test_explicit_approval_transitions_and_persists(tmp_path: Path) -> None:
    result, store, _ = _run(tmp_path)
    port = _port(tmp_path)

    record = approve_mission(store, port, result.mission_id, operator="eko")
    assert record.status == MISSION_APPROVED
    assert record.approval is not None
    assert record.approval["operator"] == "eko"
    assert record.approval["decision"] is True
    assert record.approval["frontier_sha"] == result.record.final_frontier_sha

    # Durable through a new reader, and bound to the reviewed frontier.
    fresh = MissionStore(tmp_path / "state").load(result.mission_id)
    assert fresh.status == MISSION_APPROVED
    assert fresh.approval is not None and fresh.approval["digest"]
    assert port.request_approval(
        fresh.approval["action"], {"frontier_sha": fresh.final_frontier_sha}
    )

    # Approval is a decision, not delivery: no remote, no push, no deploy.
    repo = DeliverableRepo.open(tmp_path / "project")
    assert repo.remotes() == ()
    assert repo.head() == fresh.final_frontier_sha


def test_rejection_is_preserved(tmp_path: Path) -> None:
    result, store, _ = _run(tmp_path)
    record = approve_mission(
        store, _port(tmp_path), result.mission_id, operator="eko", decision=False
    )
    assert record.status == MISSION_REJECTED
    assert MissionStore(tmp_path / "state").load(result.mission_id).status == MISSION_REJECTED


def test_blocked_mission_cannot_be_approved(tmp_path: Path) -> None:
    result, store, _ = _run(tmp_path, MissionInference(qa=BLOCK_VERDICT))
    assert result.status == MISSION_BLOCKED
    with pytest.raises(MissionLedgerError, match="only a mission awaiting approval"):
        approve_mission(store, _port(tmp_path), result.mission_id, operator="eko")


def test_mission_with_failed_final_validation_cannot_be_approved(tmp_path: Path) -> None:
    result, store, _ = _run(tmp_path, final=STRICT_FINAL)
    assert result.status == MISSION_FINAL_VALIDATION_FAILED
    with pytest.raises(MissionLedgerError, match="only a mission awaiting approval"):
        approve_mission(store, _port(tmp_path), result.mission_id, operator="eko")


def test_unknown_mission_approval_fails(tmp_path: Path) -> None:
    store = MissionStore(tmp_path / "state")
    with pytest.raises(MissionLedgerError, match="unknown mission"):
        approve_mission(
            store, _port(tmp_path), "mission-abcdef123456-00112233", operator="eko"
        )


def test_duplicate_approval_fails_closed(tmp_path: Path) -> None:
    result, store, _ = _run(tmp_path)
    port = _port(tmp_path)
    approve_mission(store, port, result.mission_id, operator="eko")
    with pytest.raises(MissionLedgerError, match="only a mission awaiting approval"):
        approve_mission(store, port, result.mission_id, operator="someone-else")
    assert MissionStore(tmp_path / "state").load(result.mission_id).approval["operator"] == "eko"


# --- Integrity-review regressions ------------------------------------------


def test_deliverable_moving_before_final_validation_blocks_the_gate(tmp_path: Path) -> None:
    """A green run must describe the very tree the gate would approve."""
    state: dict[str, int] = {"advances": 0}

    def sink_record(event: Any) -> None:
        if event.kind != "FRONTIER_ADVANCED":
            return
        state["advances"] += 1
        if state["advances"] != 3:
            return
        # A concurrent writer lands a commit after the LAST integration and
        # before the final validation reads the head.
        repo_path = tmp_path / "project"
        (repo_path / "outside.txt").write_text("out of band\n")
        _git(repo_path, "add", "-A")
        _git(repo_path, "commit", "-q", "-m", "out-of-band commit")

    class Sink:
        def record(self, event: Any) -> None:
            sink_record(event)

    store = MissionStore(tmp_path / "state")
    runner = MissionRunner(
        MissionInference(),
        store,
        workspaces_root=tmp_path / "work",
        validation_commands=VALIDATION,
        event_sink=Sink(),
    )
    result = runner.run("Create a tiny Python arithmetic package", tmp_path / "project")

    assert result.status == MISSION_BLOCKED
    assert result.record.blocker is not None
    assert result.record.blocker["reason"] == "frontier_moved"
    assert result.record.final_validation is None
    assert result.record.final_frontier_sha == ""
    assert not result.approval_required


def test_concurrent_decisions_cannot_both_succeed(tmp_path: Path) -> None:
    """Two operators deciding at once: exactly one decision is recorded."""
    result, store, _ = _run(tmp_path)
    port = _port(tmp_path)

    outcomes: list[str] = []
    errors: list[str] = []

    def decide(approve: bool) -> None:
        try:
            record = approve_mission(
                MissionStore(tmp_path / "state"),
                port,
                result.mission_id,
                operator="approver" if approve else "rejecter",
                decision=approve,
            )
            outcomes.append(record.status)
        except MissionLedgerError as exc:
            errors.append(str(exc))

    import threading

    barrier = threading.Barrier(2)

    def worker(approve: bool) -> None:
        barrier.wait()
        decide(approve)

    threads = [threading.Thread(target=worker, args=(flag,)) for flag in (True, False)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(outcomes) == 1, f"both decisions landed: {outcomes}"
    assert len(errors) == 1
    final = MissionStore(tmp_path / "state").load(result.mission_id)
    assert final.status == outcomes[0]
    assert final.approval is not None
    # The recorded operator matches the decision that actually won.
    expected = "approver" if final.status == MISSION_APPROVED else "rejecter"
    assert final.approval["operator"] == expected


def test_failed_port_write_releases_the_decision_claim(tmp_path: Path) -> None:
    """A decision that never completed must not lock the mission forever."""
    result, store, _ = _run(tmp_path)

    class BrokenPort:
        def approve(self, *args: Any, **kwargs: Any) -> str:
            raise OSError("approval store unavailable")

    with pytest.raises(OSError):
        approve_mission(store, BrokenPort(), result.mission_id, operator="eko")
    # The operator can retry once the store is healthy again.
    record = approve_mission(store, _port(tmp_path), result.mission_id, operator="eko")
    assert record.status == MISSION_APPROVED
