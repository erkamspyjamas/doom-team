"""Tests for the durable mission ledger and its state-truth guarantees."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from doom_team.domain import Mission, Objective, TaskContract
from doom_team.durable import StateCorruptionError
from doom_team.mission import (
    MISSION_AWAITING_HUMAN_APPROVAL,
    MISSION_BLOCKED,
    MISSION_FINAL_VALIDATION_FAILED,
    MISSION_RUNNING,
    TASK_ACCEPTED,
    TASK_INTEGRATED,
    MissionLedgerError,
    MissionStore,
    new_mission_id,
)
from doom_team.routing import route_task
from doom_team.task_runner import RoleVerdict, TaskExecutionResult
from doom_team.validation import CommandResult, ValidationResult


def _mission() -> Mission:
    objective = Objective(id="obj1234abcd", description="Build a tiny package")
    return Mission(
        id="mission-obj1234abcd",
        objective=objective,
        tasks=(
            TaskContract(
                id="code",
                objective_id=objective.id,
                instruction="Write the module",
                acceptance_criteria=("module exists",),
                required_capabilities=("backend.python",),
            ),
            TaskContract(
                id="docs",
                objective_id=objective.id,
                instruction="Write the README",
                acceptance_criteria=("readme exists",),
                dependencies=("code",),
                required_capabilities=("documentation",),
            ),
        ),
    )


def _validation(passed: bool = True, label: str = "tests") -> ValidationResult:
    return ValidationResult(
        label=label,
        command=CommandResult(
            argv=("python", "-m", "pytest"),
            cwd="/tmp/ws",
            exit_code=0 if passed else 1,
            stdout="1 passed" if passed else "1 failed",
            stderr="",
            duration_seconds=0.5,
            timed_out=False,
        ),
    )


def _accepted_result(task_id: str = "code") -> TaskExecutionResult:
    return TaskExecutionResult(
        task_id=task_id,
        specialist_id="python_backend",
        baseline_sha="a" * 40,
        status="accepted",
        candidate_sha="b" * 40,
        changed_files=("arith.py",),
        implementation_summary="implemented",
        validation_results=(_validation(),),
        qa=RoleVerdict(role="qa", verdict="pass", summary="ok"),
        reviewer=RoleVerdict(role="reviewer", verdict="pass", summary="ok"),
    )


def _store(tmp_path: Path, secrets: tuple[str, ...] = ()) -> MissionStore:
    return MissionStore(tmp_path / "state", secret_values=secrets)


def _started(store: MissionStore, mission: Mission) -> str:
    mission_id = new_mission_id(mission.id)
    store.create(
        mission_id=mission_id,
        objective=mission.objective.description,
        plan_mission_id=mission.id,
        objective_id=mission.objective.id,
    )
    store.record_plan(mission_id, mission)
    store.record_deliverable(
        mission_id, deliverable_path="/tmp/project", baseline_sha="a" * 40
    )
    store.record_routing(mission_id, tuple(route_task(t) for t in mission.tasks))
    return mission_id


# --- 1-4: mission, objective, plan and routing persist ---------------------


def test_mission_created_durably_with_objective_and_plan(tmp_path: Path) -> None:
    store = _store(tmp_path)
    mission = _mission()
    mission_id = _started(store, mission)

    reader = MissionStore(tmp_path / "state")  # a NEW store instance
    record = reader.load(mission_id)
    assert record.mission_id == mission_id
    assert record.objective == "Build a tiny package"
    assert record.plan_mission_id == mission.id
    assert record.status == MISSION_RUNNING
    assert [t.task_id for t in record.tasks] == ["code", "docs"]
    assert record.task("docs").dependencies == ("code",)
    assert record.task("code").required_capabilities == ("backend.python",)
    assert record.baseline_sha == "a" * 40


def test_routing_decisions_persist_per_task(tmp_path: Path) -> None:
    store = _store(tmp_path)
    mission_id = _started(store, _mission())
    record = MissionStore(tmp_path / "state").load(mission_id)
    routed = {item["task_id"]: item["specialist_id"] for item in record.routing}
    assert routed == {"code": "python_backend", "docs": "technical_writer"}
    assert record.task("code").specialist_id == "python_backend"
    assert record.task("docs").specialist_id == "technical_writer"
    assert all(item["rationale"] for item in record.routing)


def test_mission_id_is_filesystem_safe_and_unique(tmp_path: Path) -> None:
    mission = _mission()
    ids = {new_mission_id(mission.id) for _ in range(50)}
    assert len(ids) == 50  # distinct historical runs of the same objective
    for value in ids:
        assert set(value) <= set("mission-0123456789abcdef")
        assert "/" not in value and ".." not in value
    # The objective text itself never appears in the identity.
    assert "Build" not in next(iter(ids))


def test_unsafe_mission_id_is_refused(tmp_path: Path) -> None:
    store = _store(tmp_path)
    for bad in ("../escape", "mission-x", "mission-abc123-zz", "", "mission-abc123-11/../x"):
        with pytest.raises(MissionLedgerError, match="unsafe mission id"):
            store.load(bad)


# --- 5: task results persist, derived from real result objects -------------


def test_task_result_persists_derived_state(tmp_path: Path) -> None:
    store = _store(tmp_path)
    mission_id = _started(store, _mission())
    store.record_task_started(mission_id, "code", baseline_sha="a" * 40)
    store.record_task_result(mission_id, _accepted_result())

    record = MissionStore(tmp_path / "state").load(mission_id)
    task = record.task("code")
    assert task.state == TASK_ACCEPTED
    assert task.candidate_sha == "b" * 40
    assert task.changed_files == ("arith.py",)
    assert task.validation[0]["passed"] is True
    assert task.qa is not None and task.qa["verdict"] == "pass"
    assert task.reviewer is not None and task.reviewer["verdict"] == "pass"


def test_ledger_refuses_accepted_without_the_full_chain(tmp_path: Path) -> None:
    """A forged result cannot buy an accepted task."""
    store = _store(tmp_path)
    mission_id = _started(store, _mission())
    store.record_task_started(mission_id, "code", baseline_sha="a" * 40)
    full = _accepted_result()
    for broken in (
        # claims accepted with nothing behind it
        TaskExecutionResult(
            task_id="code", specialist_id="python_backend", baseline_sha="a" * 40,
            status="accepted",
        ),
        # no QA verdict
        TaskExecutionResult(**{**full.__dict__, "qa": None}),
        # no Reviewer verdict
        TaskExecutionResult(**{**full.__dict__, "reviewer": None}),
        # QA blocked but status claims accepted
        TaskExecutionResult(
            **{**full.__dict__, "qa": RoleVerdict(role="qa", verdict="block", summary="no")}
        ),
        # validation actually failed
        TaskExecutionResult(**{**full.__dict__, "validation_results": (_validation(False),)}),
        # no validation ran at all
        TaskExecutionResult(**{**full.__dict__, "validation_results": ()}),
        # no candidate commit
        TaskExecutionResult(**{**full.__dict__, "candidate_sha": None}),
    ):
        with pytest.raises(MissionLedgerError, match="without the full"):
            store.record_task_result(mission_id, broken)
    # The task is still recorded as running, never accepted.
    assert MissionStore(tmp_path / "state").load(mission_id).task("code").state == "running"


def test_blocked_task_result_is_recorded_with_its_status(tmp_path: Path) -> None:
    store = _store(tmp_path)
    mission_id = _started(store, _mission())
    store.record_task_started(mission_id, "code", baseline_sha="a" * 40)
    blocked = TaskExecutionResult(
        task_id="code",
        specialist_id="python_backend",
        baseline_sha="a" * 40,
        status="qa_blocked",
        candidate_sha="b" * 40,
        validation_results=(_validation(),),
        qa=RoleVerdict(role="qa", verdict="block", summary="defect", blockers=("bug",)),
    )
    record = store.record_task_result(mission_id, blocked)
    assert record.task("code").state == "qa_blocked"
    assert record.task("code").accepted is False


# --- frontier truth --------------------------------------------------------


def test_only_accepted_tasks_advance_the_frontier(tmp_path: Path) -> None:
    store = _store(tmp_path)
    mission_id = _started(store, _mission())
    store.record_task_started(mission_id, "code", baseline_sha="a" * 40)
    blocked = TaskExecutionResult(
        task_id="code", specialist_id="python_backend", baseline_sha="a" * 40,
        status="reviewer_blocked", candidate_sha="b" * 40,
        validation_results=(_validation(),),
        qa=RoleVerdict(role="qa", verdict="pass", summary="ok"),
        reviewer=RoleVerdict(role="reviewer", verdict="block", summary="unsafe"),
    )
    store.record_task_result(mission_id, blocked)
    with pytest.raises(MissionLedgerError, match="only an accepted candidate"):
        store.record_frontier_advanced(mission_id, "code", frontier_sha="c" * 40)


def test_task_cannot_start_from_a_stale_frontier(tmp_path: Path) -> None:
    store = _store(tmp_path)
    mission_id = _started(store, _mission())
    store.record_task_started(mission_id, "code", baseline_sha="a" * 40)
    store.record_task_result(mission_id, _accepted_result())
    store.record_frontier_advanced(mission_id, "code", frontier_sha="c" * 40)
    with pytest.raises(MissionLedgerError, match="not the current frontier"):
        store.record_task_started(mission_id, "docs", baseline_sha="a" * 40)
    # Starting from the advanced frontier is accepted.
    record = store.record_task_started(mission_id, "docs", baseline_sha="c" * 40)
    assert record.task("docs").baseline_sha == "c" * 40


# --- 6: final validation persists and gates approval -----------------------


def _integrate_all(store: MissionStore, mission_id: str) -> None:
    store.record_task_started(mission_id, "code", baseline_sha="a" * 40)
    store.record_task_result(mission_id, _accepted_result("code"))
    store.record_frontier_advanced(mission_id, "code", frontier_sha="c" * 40)
    store.record_task_started(mission_id, "docs", baseline_sha="c" * 40)
    docs = TaskExecutionResult(**{**_accepted_result("docs").__dict__, "task_id": "docs"})
    store.record_task_result(mission_id, docs)
    store.record_frontier_advanced(mission_id, "docs", frontier_sha="d" * 40)


def test_final_validation_requires_every_task_integrated(tmp_path: Path) -> None:
    store = _store(tmp_path)
    mission_id = _started(store, _mission())
    store.record_task_started(mission_id, "code", baseline_sha="a" * 40)
    store.record_task_result(mission_id, _accepted_result())
    store.record_frontier_advanced(mission_id, "code", frontier_sha="c" * 40)
    with pytest.raises(MissionLedgerError, match="not integrated"):
        store.record_final_validation(mission_id, (_validation(),), frontier_sha="c" * 40)


def test_final_validation_persists_and_passes_gate(tmp_path: Path) -> None:
    store = _store(tmp_path)
    mission_id = _started(store, _mission())
    _integrate_all(store, mission_id)
    store.record_final_validation(
        mission_id, (_validation(), _validation(label="compile")), frontier_sha="d" * 40
    )
    record = store.record_awaiting_approval(mission_id)
    assert record.status == MISSION_AWAITING_HUMAN_APPROVAL
    assert record.final_frontier_sha == "d" * 40
    reader = MissionStore(tmp_path / "state").load(mission_id)
    assert reader.final_validation is not None and len(reader.final_validation) == 2
    assert reader.final_validation_passed is True
    assert all(t.state == TASK_INTEGRATED for t in reader.tasks)


def test_failed_final_validation_blocks_the_approval_gate(tmp_path: Path) -> None:
    store = _store(tmp_path)
    mission_id = _started(store, _mission())
    _integrate_all(store, mission_id)
    record = store.record_final_validation(
        mission_id, (_validation(False),), frontier_sha="d" * 40
    )
    assert record.status == MISSION_FINAL_VALIDATION_FAILED
    assert record.final_validation_passed is False
    with pytest.raises(MissionLedgerError, match="not running"):
        store.record_awaiting_approval(mission_id)


def test_approval_gate_refuses_without_any_final_validation(tmp_path: Path) -> None:
    store = _store(tmp_path)
    mission_id = _started(store, _mission())
    _integrate_all(store, mission_id)
    with pytest.raises(MissionLedgerError, match="without final validation"):
        store.record_awaiting_approval(mission_id)


def test_approval_gate_refuses_with_unintegrated_tasks(tmp_path: Path) -> None:
    store = _store(tmp_path)
    mission_id = _started(store, _mission())
    with pytest.raises(MissionLedgerError, match="unintegrated tasks"):
        store.record_awaiting_approval(mission_id)


# --- 7-8: approval state and secret hygiene --------------------------------


def test_blocked_mission_is_terminal_for_this_layer(tmp_path: Path) -> None:
    store = _store(tmp_path)
    mission_id = _started(store, _mission())
    record = store.record_blocked(
        mission_id, reason="qa_blocked", detail="QA found a defect", task_id="code"
    )
    assert record.status == MISSION_BLOCKED
    assert record.blocker == {
        "reason": "qa_blocked",
        "detail": "QA found a defect",
        "task_id": "code",
    }
    # No retry seam: a blocked mission accepts no further execution records.
    with pytest.raises(MissionLedgerError, match="not running"):
        store.record_task_started(mission_id, "code", baseline_sha="a" * 40)


def test_secrets_are_never_written_to_the_ledger(tmp_path: Path) -> None:
    secret = "super-secret-api-key"
    store = _store(tmp_path, secrets=(secret,))
    mission = Mission(
        id="mission-obj1234abcd",
        objective=Objective(id="obj1234abcd", description=f"Build with key {secret}"),
        tasks=_mission().tasks,
    )
    mission_id = _started(store, mission)
    path = (tmp_path / "state" / "missions" / f"{mission_id}.json")
    raw = path.read_text()
    assert secret not in raw
    assert "[REDACTED]" in raw
    assert secret not in json.dumps(MissionStore(tmp_path / "state").load(mission_id).to_dict())


def test_tampered_ledger_fails_closed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    mission_id = _started(store, _mission())
    path = tmp_path / "state" / "missions" / f"{mission_id}.json"
    payload = json.loads(path.read_text())
    payload["status"] = MISSION_AWAITING_HUMAN_APPROVAL  # forge an approval-ready mission
    path.write_text(json.dumps(payload))
    with pytest.raises(StateCorruptionError):
        MissionStore(tmp_path / "state").load(mission_id)


def test_duplicate_mission_creation_is_refused(tmp_path: Path) -> None:
    store = _store(tmp_path)
    mission = _mission()
    mission_id = _started(store, mission)
    with pytest.raises(MissionLedgerError, match="already exists"):
        store.create(
            mission_id=mission_id,
            objective="anything",
            plan_mission_id=mission.id,
            objective_id=mission.objective.id,
        )


# --- Integrity-review regressions ------------------------------------------


def _gated(store: MissionStore) -> str:
    """Drive a mission honestly to the approval gate."""
    mission_id = _started(store, _mission())
    _integrate_all(store, mission_id)
    store.record_final_validation(mission_id, (_validation(),), frontier_sha="d" * 40)
    store.record_awaiting_approval(mission_id)
    return mission_id


def test_result_status_outside_the_known_vocabulary_is_refused(tmp_path: Path) -> None:
    """A result cannot name a privileged state the ledger alone may assign."""
    store = _store(tmp_path)
    mission_id = _started(store, _mission())
    store.record_task_started(mission_id, "code", baseline_sha="a" * 40)
    for status in ("integrated", "pending", "running", "approved", "totally-made-up"):
        forged = TaskExecutionResult(
            task_id="code",
            specialist_id="python_backend",
            baseline_sha="a" * 40,
            status=status,
        )
        with pytest.raises(MissionLedgerError, match="unknown status"):
            store.record_task_result(mission_id, forged)
    record = MissionStore(tmp_path / "state").load(mission_id)
    assert record.task("code").state == "running"
    assert record.task("code").integrated is False
    assert record.all_tasks_integrated is False


def test_final_validation_must_describe_the_current_frontier(tmp_path: Path) -> None:
    """Green evidence for another tree can never certify this one."""
    store = _store(tmp_path)
    mission_id = _started(store, _mission())
    _integrate_all(store, mission_id)
    with pytest.raises(MissionLedgerError, match="not the mission frontier"):
        store.record_final_validation(mission_id, (_validation(),), frontier_sha="e" * 40)
    record = store.record_final_validation(
        mission_id, (_validation(),), frontier_sha="d" * 40
    )
    assert record.final_validation_sha == "d" * 40
    assert store.record_awaiting_approval(mission_id).final_frontier_sha == "d" * 40


def test_only_one_decision_can_ever_be_taken(tmp_path: Path) -> None:
    """The approval gate is a mutex held for the whole decision."""
    store = _store(tmp_path)
    mission_id = _gated(store)

    with store.decision_lock(mission_id):
        with pytest.raises(MissionLedgerError, match="decision in progress"):
            with store.decision_lock(mission_id):
                pass
    # An abandoned decision releases the lock: the mission stays decidable.
    with store.decision_lock(mission_id):
        store.record_approval_decision(
            mission_id, operator="eko", decision=True, digest="sha256:x", action="mission:x"
        )
    with pytest.raises(MissionLedgerError, match="only a mission awaiting"):
        with store.decision_lock(mission_id):
            pass


def test_interrupted_decision_never_wedges_the_mission(tmp_path: Path) -> None:
    """A killed decider must not lock a finished mission out of every decision."""
    store = _store(tmp_path)
    mission_id = _gated(store)

    with pytest.raises(KeyboardInterrupt):
        with store.decision_lock(mission_id):
            raise KeyboardInterrupt  # operator hits Ctrl-C mid-decision
    # The mission is still decidable by the next operator.
    with store.decision_lock(mission_id) as record:
        assert record.status == MISSION_AWAITING_HUMAN_APPROVAL
        decided = store.record_approval_decision(
            mission_id, operator="eko", decision=True, digest="d", action="a"
        )
    assert decided.status == "approved"


def test_decision_lock_is_not_mistaken_for_a_mission(tmp_path: Path) -> None:
    store = _store(tmp_path)
    mission_id = _gated(store)
    with store.decision_lock(mission_id):
        assert store.list_ids() == (mission_id,)


def test_decision_lock_refuses_an_unsafe_mission_id(tmp_path: Path) -> None:
    store = _store(tmp_path)
    for bad in ("../escape", "mission-x", "../../etc/passwd"):
        with pytest.raises(MissionLedgerError, match="unsafe mission id"):
            with store.decision_lock(bad):
                pass


def test_returned_record_matches_what_was_persisted(tmp_path: Path) -> None:
    """No secret survives in a returned record that is absent from disk."""
    secret = "sk-live-DEADBEEF"
    store = _store(tmp_path, secrets=(secret,))
    mission = Mission(
        id="mission-obj1234abcd",
        objective=Objective(id="obj1234abcd", description="Build it"),
        tasks=_mission().tasks,
    )
    mission_id = _started(store, mission)
    returned = store.record_blocked(
        mission_id, reason="deliverable_unavailable", detail=f"path {secret} is not empty"
    )
    assert secret not in json.dumps(returned.to_dict())
    assert returned.blocker is not None and "[REDACTED]" in returned.blocker["detail"]
    on_disk = MissionStore(tmp_path / "state").load(mission_id)
    assert returned.to_dict() == on_disk.to_dict()


def test_status_lookalike_objects_cannot_forge_a_privileged_state(tmp_path: Path) -> None:
    """A rigged __eq__ must not match one status while carrying another."""

    class EqStr(str):
        def __eq__(self, other: object) -> bool:
            return other == "validation_failed"

        def __hash__(self) -> int:
            return hash("validation_failed")

    class EnumLike:
        def __eq__(self, other: object) -> bool:
            return other == "qa_blocked"

        def __hash__(self) -> int:
            return hash("qa_blocked")

        def __str__(self) -> str:
            return "integrated"

    store = _store(tmp_path)
    mission_id = _started(store, _mission())
    store.record_task_started(mission_id, "code", baseline_sha="a" * 40)
    for status, reason in (
        (EqStr("integrated"), "unknown status"),
        (EnumLike(), "unknown status"),
        (EqStr("accepted"), "without the full"),  # normalized, then chain-checked
    ):
        with pytest.raises(MissionLedgerError, match=reason):
            store.record_task_result(
                mission_id,
                TaskExecutionResult(
                    task_id="code",
                    specialist_id="python_backend",
                    baseline_sha="a" * 40,
                    status=status,  # type: ignore[arg-type]
                ),
            )
    record = MissionStore(tmp_path / "state").load(mission_id)
    assert record.task("code").state == "running"
    assert record.all_tasks_integrated is False


def test_secret_straddling_the_clip_boundary_is_still_redacted(tmp_path: Path) -> None:
    """Redaction runs before truncation, so no half-cut credential survives."""
    from doom_team.mission import MAX_LEDGER_TEXT_CHARS

    secret = "sk-live-deadbeefcafe0123"
    store = _store(tmp_path, secrets=(secret,))
    mission_id = _started(store, _mission())
    # Place the secret so the clip boundary falls inside it.
    detail = "x" * (MAX_LEDGER_TEXT_CHARS - len(secret) + 1) + secret + " trailing"
    record = store.record_blocked(mission_id, reason="deliverable_unavailable", detail=detail)
    assert record.blocker is not None
    assert secret[:-1] not in record.blocker["detail"]
    raw = (tmp_path / "state" / "missions" / f"{mission_id}.json").read_text()
    assert secret[:-1] not in raw


def test_plan_identifiers_carrying_secrets_are_refused(tmp_path: Path) -> None:
    """Identifiers are stored verbatim, so a secret-bearing id fails closed."""
    secret = "sk-live-deadbeefcafe0123"
    store = _store(tmp_path, secrets=(secret,))
    objective = Objective(id="obj1234abcd", description="Build it")
    mission = Mission(
        id="mission-obj1234abcd",
        objective=objective,
        tasks=(
            TaskContract(
                id=f"impl-{secret}",
                objective_id=objective.id,
                instruction="Write the module",
                acceptance_criteria=("module exists",),
                required_capabilities=("backend.python",),
            ),
        ),
    )
    mission_id = new_mission_id(mission.id)
    store.create(
        mission_id=mission_id,
        objective=objective.description,
        plan_mission_id=mission.id,
        objective_id=objective.id,
    )
    with pytest.raises(MissionLedgerError, match="contains redacted content"):
        store.record_plan(mission_id, mission)
    assert secret not in (tmp_path / "state" / "missions" / f"{mission_id}.json").read_text()
