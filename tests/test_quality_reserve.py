"""The one-shot quality-feedback remediation reserve, and lossless output tails.

The first valid standalone smoke exposed a positional convergence gap: QA and
the Reviewer only ever speak on a validation-passing attempt, so their first
verdict can land on the final budget slot carrying actionable feedback no
attempt can consume. The reserve guarantees exactly one remediation attempt
for a task's FIRST quality blocker when - and only when - that blocker was
the final ordinary attempt. Everything stays bounded: the hard ceiling is
``max_attempts_per_task + 1``, once per task, pinned per mission at creation.

The same smoke proved the companion evidence defect: validation output was
head-clipped for the ledger, and remediation then took a tail OF THE CLIP -
deleting exactly the terminal exception line pytest prints last.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from doom_team.durable import atomic_write_json, read_json_checked
from doom_team.mission import (
    MAX_LEDGER_OUTPUT_CHARS,
    MISSION_AWAITING_HUMAN_APPROVAL,
    MISSION_BLOCKED,
    MissionLedgerError,
    MissionStore,
    quality_reserve_available,
    remediation_context,
)
from doom_team.mission_runner import MissionRunner
from doom_team.task_runner import TaskExecutionResult
from doom_team.validation import CommandResult, ValidationResult
from tests.test_mission_recovery import (
    BROKEN_CODE,
    CODE,
    DOCS,
    GOOD_CODE,
    MULTI_PLAN,
    SINGLE_PLAN,
    AttemptScript,
    _run,
)
from tests.test_mission_runner import BLOCK_VERDICT, GOOD_WORK, VALIDATION

GOOD = {"files": GOOD_CODE}
BROKEN = {"files": BROKEN_CODE}
QA_BLOCK = {"files": GOOD_CODE, "qa": BLOCK_VERDICT}
REVIEW_BLOCK = {"files": GOOD_CODE, "reviewer": BLOCK_VERDICT}
# Validation-passing but DIFFERENT bytes from GOOD_CODE, so a blocked
# candidate and its remediation commit distinct Git SHAs and integration
# provenance is checkable. Identical files on one baseline share a SHA.
GOOD_CODE_V2 = dict(GOOD_CODE, **{"arith.py": GOOD_CODE["arith.py"] + "# v2\n"})
REVIEW_BLOCK_V2 = {"files": GOOD_CODE_V2, "reviewer": BLOCK_VERDICT}


# --- Q1/Q2: a final-slot quality block earns exactly one remediation attempt


@pytest.mark.parametrize("block,blocked_status", [
    (REVIEW_BLOCK, "reviewer_blocked"),
    (QA_BLOCK, "qa_blocked"),
])
def test_final_slot_quality_block_gets_one_remediation_attempt(
    tmp_path: Path, block: dict[str, Any], blocked_status: str
) -> None:
    # base max = 2: validation failure burns slot 1, the first quality
    # verdict lands on slot 2 with nothing left - the exact smoke shape.
    script = AttemptScript(SINGLE_PLAN, {CODE: [BROKEN, block, GOOD]})
    result, _ = _run(tmp_path, script, semantic_retries=1)

    assert result.status == MISSION_AWAITING_HUMAN_APPROVAL
    task = result.record.task("arith-code")
    assert [a.attempt for a in task.attempts] == [1, 2, 3]
    assert [a.status for a in task.attempts] == [
        "validation_failed", blocked_status, "accepted",
    ]
    assert task.integrated
    # The remediation attempt received the actual quality blocker.
    assert "missing edge case" in task.attempts[2].remediation


def test_quality_attempt_passes_all_normal_gates_and_starts_from_frontier(
    tmp_path: Path,
) -> None:
    script = AttemptScript(SINGLE_PLAN, {CODE: [BROKEN, REVIEW_BLOCK_V2, GOOD]})
    result, _ = _run(tmp_path, script, semantic_retries=1)
    task = result.record.task("arith-code")
    extra = task.attempts[2]
    # Full ordinary attempt: fresh workspace, frontier baseline, every gate.
    assert extra.baseline_sha == result.record.baseline_sha
    assert extra.workspace_dir != task.attempts[1].workspace_dir
    assert extra.validation and all(v["passed"] for v in extra.validation)
    assert extra.qa is not None and extra.reviewer is not None
    # The blocked candidate itself was never integrated.
    assert task.integrated_sha == extra.candidate_sha
    assert task.integrated_sha != task.attempts[1].candidate_sha


# --- Q3/Q4: non-quality exhaustion earns nothing --------------------------


def test_validation_exhaustion_gets_no_reserve(tmp_path: Path) -> None:
    script = AttemptScript(SINGLE_PLAN, {CODE: [BROKEN, BROKEN, GOOD]})
    result, store = _run(tmp_path, script, semantic_retries=1)
    assert result.status == MISSION_BLOCKED
    task = result.record.task("arith-code")
    assert len(task.attempts) == 2
    assert result.record.blocker is not None
    assert result.record.blocker["reason"] == "recovery_exhausted"
    with pytest.raises(MissionLedgerError):
        store.record_task_started(
            result.record.mission_id, "arith-code",
            baseline_sha=result.record.frontier_sha,
        )


def test_execution_exhaustion_gets_no_reserve(tmp_path: Path) -> None:
    class AlwaysMalformed(AttemptScript):
        def complete(self, messages: list[dict[str, str]]) -> Any:
            system = messages[0]["content"]
            if "implementation specialist for exactly one" in system:
                from tests.test_mission_runner import _Response

                return _Response("here is the code, boss!")
            return super().complete(messages)

    script = AlwaysMalformed(SINGLE_PLAN, {CODE: [GOOD]})
    result, _ = _run(tmp_path, script, semantic_retries=1)
    assert result.status == MISSION_BLOCKED
    task = result.record.task("arith-code")
    assert [a.status for a in task.attempts] == ["execution_failed"] * 2


# --- Q5: an early first quality block already had its subsequent attempts --


def test_early_first_quality_block_earns_no_extra_slot(tmp_path: Path) -> None:
    # base max = 3; the FIRST reviewer feedback arrives on attempt 1 and gets
    # ordinary attempts 2 and 3. A later block on the final slot earns
    # nothing: the invariant is first-feedback-gets-a-successor, not
    # every-block-adds-one.
    script = AttemptScript(
        SINGLE_PLAN, {CODE: [REVIEW_BLOCK, REVIEW_BLOCK, REVIEW_BLOCK, GOOD]}
    )
    result, store = _run(tmp_path, script, semantic_retries=2)
    assert result.status == MISSION_BLOCKED
    task = result.record.task("arith-code")
    assert len(task.attempts) == 3
    assert quality_reserve_available(result.record, "arith-code") is False


# --- Q6/Q7: the reserve attempt is the last word ---------------------------


@pytest.mark.parametrize("final", [BROKEN, REVIEW_BLOCK, QA_BLOCK])
def test_failed_quality_remediation_ends_the_task(
    tmp_path: Path, final: dict[str, Any]
) -> None:
    script = AttemptScript(SINGLE_PLAN, {CODE: [BROKEN, REVIEW_BLOCK, final, GOOD]})
    result, store = _run(tmp_path, script, semantic_retries=1)
    assert result.status == MISSION_BLOCKED
    task = result.record.task("arith-code")
    assert len(task.attempts) == 3  # hard cap: base 2 + the one reserve
    assert quality_reserve_available(result.record, "arith-code") is False
    # Blocked missions refuse new attempts outright; the count gate behind
    # it is exercised by test_quality_slot_cannot_be_double_allocated.
    with pytest.raises(MissionLedgerError):
        store.record_task_started(
            result.record.mission_id, "arith-code",
            baseline_sha=result.record.frontier_sha,
        )


# --- Q8/Q9: restart neither forgets nor replenishes the reserve ------------


class Crash(RuntimeError):
    pass


def test_restart_before_quality_attempt_still_allows_exactly_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = AttemptScript(SINGLE_PLAN, {CODE: [BROKEN, REVIEW_BLOCK, GOOD]})
    original = MissionRunner._one_attempt
    calls = {"n": 0}

    def crash_on_third(self: MissionRunner, *args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 3:
            raise Crash("process died before the quality attempt started")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(MissionRunner, "_one_attempt", crash_on_third)
    with pytest.raises(Crash):
        _run(tmp_path, script, semantic_retries=1)
    monkeypatch.setattr(MissionRunner, "_one_attempt", original)

    store = MissionStore(tmp_path / "state")
    mission_id = store.list_ids()[0]
    record = store.load(mission_id)
    assert len(record.task("arith-code").attempts) == 2
    assert quality_reserve_available(record, "arith-code") is True

    resumed = MissionRunner(
        script, store, workspaces_root=tmp_path / "work",
        validation_commands=VALIDATION, semantic_retries=1,
    ).resume(mission_id)
    assert resumed.status == MISSION_AWAITING_HUMAN_APPROVAL
    task = resumed.record.task("arith-code")
    assert len(task.attempts) == 3
    assert task.attempts[2].status == "accepted"


def test_restart_after_quality_attempt_creates_no_second_reserve(
    tmp_path: Path,
) -> None:
    script = AttemptScript(SINGLE_PLAN, {CODE: [BROKEN, REVIEW_BLOCK, REVIEW_BLOCK]})
    result, store = _run(tmp_path, script, semantic_retries=1)
    assert result.status == MISSION_BLOCKED
    # A new process sees the same durable truth: reserve spent, task done.
    fresh = MissionStore(tmp_path / "state")
    record = fresh.load(result.record.mission_id)
    assert len(record.task("arith-code").attempts) == 3
    assert quality_reserve_available(record, "arith-code") is False
    resumed = MissionRunner(
        script, fresh, workspaces_root=tmp_path / "work",
        validation_commands=VALIDATION, semantic_retries=1,
    ).resume(record.mission_id)
    assert resumed.status == MISSION_BLOCKED
    assert len(resumed.record.task("arith-code").attempts) == 3


# --- Q10: the slot cannot be allocated twice -------------------------------


def test_quality_slot_cannot_be_double_allocated(tmp_path: Path) -> None:
    script = AttemptScript(SINGLE_PLAN, {CODE: [BROKEN, REVIEW_BLOCK, GOOD]})
    result, store = _run(tmp_path, script, semantic_retries=1)
    record = result.record
    task = record.task("arith-code")
    assert len(task.attempts) == 3
    # However many readers race, durable history admits no fourth attempt.
    with pytest.raises(MissionLedgerError):
        store.record_task_started(
            record.mission_id, "arith-code", baseline_sha=record.frontier_sha
        )


def _quality_blocked_result(role: str) -> TaskExecutionResult:
    from doom_team.task_runner import RoleVerdict

    passing = ValidationResult(
        label="tests",
        command=CommandResult(
            argv=("pytest",), cwd=".", exit_code=0, stdout="ok", stderr="",
            duration_seconds=0.1, timed_out=False,
        ),
    )
    block = RoleVerdict(role=role, verdict="block", summary="s", blockers=("b",))
    return TaskExecutionResult(
        task_id="arith-code", specialist_id="python_backend", baseline_sha="",
        status=f"{role}_blocked", candidate_sha="a" * 40,
        validation_results=(passing,),
        qa=block if role == "qa" else RoleVerdict(role="qa", verdict="pass", summary=""),
        reviewer=block if role == "reviewer" else None,
    )


def test_ledger_hard_cap_holds_on_a_running_mission(tmp_path: Path) -> None:
    """The count gate itself: base 1 + reserve 1, then refusal - while the
    mission is still running, so no earlier status guard can mask it."""
    store, mission_id = _record_failure(tmp_path, "x")  # attempt 1: validation_failed
    record = store.load(mission_id)
    assert quality_reserve_available(record, "arith-code") is False  # not quality

    # Rebuild in a fresh store with base max 1: first attempt reviewer-blocks.
    from doom_team.domain import Mission, Objective, TaskContract

    store2 = MissionStore(tmp_path / "cap" / "state")
    rec = store2.create(
        mission_id="mission-abcdef-01234567", objective="obj",
        plan_mission_id="p", objective_id="o", max_attempts_per_task=1,
    )
    mission = Mission(
        id="m", objective=Objective(id="o", description="obj"),
        tasks=(TaskContract(
            id="arith-code", objective_id="o", instruction="x",
            acceptance_criteria=("y",),
        ),),
    )
    store2.record_plan(rec.mission_id, mission)
    store2.record_task_started(rec.mission_id, "arith-code", baseline_sha="")
    store2.record_task_result(rec.mission_id, _quality_blocked_result("reviewer"))
    assert quality_reserve_available(store2.load(rec.mission_id), "arith-code") is True

    # The one reserve attempt opens; once terminal, nothing more opens.
    store2.record_task_started(rec.mission_id, "arith-code", baseline_sha="")
    store2.record_attempt_interrupted(rec.mission_id, "arith-code")
    loaded = store2.load(rec.mission_id)
    assert loaded.status == "running"
    assert len(loaded.task("arith-code").attempts) == 2  # hard cap 1 + 1
    assert quality_reserve_available(loaded, "arith-code") is False
    with pytest.raises(MissionLedgerError, match="used all"):
        store2.record_task_started(rec.mission_id, "arith-code", baseline_sha="")


def test_forged_or_noncanonical_status_earns_no_reserve(tmp_path: Path) -> None:
    """Only the canonical qa_blocked/reviewer_blocked strings qualify."""
    import dataclasses

    store, mission_id = _record_failure(tmp_path, "x")
    record = store.load(mission_id)
    task = record.task("arith-code")
    # A canonical status whose verdict payload is missing is equally inert:
    # there is no feedback to remediate, so it must not open the reserve.
    bare = dataclasses.replace(
        record,
        quality_reserve_per_task=1,
        max_attempts_per_task=1,
        tasks=(dataclasses.replace(
            task,
            attempts=tuple(
                dataclasses.replace(a, status="reviewer_blocked")
                for a in task.attempts
            ),
        ),),
    )
    assert bare.task("arith-code").attempts[0].reviewer is None
    assert quality_reserve_available(bare, "arith-code") is False

    for fake in ("Reviewer_Blocked", "reviewer_blocked ", "quality_blocked", ""):
        forged = dataclasses.replace(
            record,
            quality_reserve_per_task=1,
            tasks=(dataclasses.replace(
                task,
                attempts=tuple(
                    dataclasses.replace(a, status=fake) for a in task.attempts
                ),
            ),),
        )
        # attempts==max shape with a non-canonical status: never eligible.
        forged = dataclasses.replace(forged, max_attempts_per_task=1)
        assert quality_reserve_available(forged, "arith-code") is False


# --- L1-L4: policy is pinned; old missions stay on old policy --------------


def _strip_reserve_field(state_dir: Path, mission_id: str) -> None:
    """Rewrite a record the way pre-reserve software wrote it: no field."""
    path = state_dir / "missions" / f"{mission_id}.json"
    payload = read_json_checked(path)
    payload.pop("quality_reserve_per_task", None)
    atomic_write_json(path, payload)


def test_legacy_record_without_field_has_no_reserve(tmp_path: Path) -> None:
    script = AttemptScript(SINGLE_PLAN, {CODE: [BROKEN, REVIEW_BLOCK, GOOD]})
    result, store = _run(tmp_path, script, semantic_retries=1)
    assert len(result.record.task("arith-code").attempts) == 3

    # Same eligible-shaped history, written by pre-reserve software.
    legacy = AttemptScript(SINGLE_PLAN, {CODE: [BROKEN, REVIEW_BLOCK, GOOD]})
    legacy_dir = tmp_path / "legacy"
    # Crash the third attempt so the durable state is exactly "final ordinary
    # attempt reviewer-blocked" before we rewrite it as a legacy record.
    store2 = None
    original = MissionRunner._one_attempt
    calls = {"n": 0}

    def crash_on_third(self: MissionRunner, *args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 3:
            raise Crash("stop before quality attempt")
        return original(self, *args, **kwargs)

    MissionRunner._one_attempt = crash_on_third  # type: ignore[method-assign]
    try:
        with pytest.raises(Crash):
            store2 = MissionStore(legacy_dir / "state")
            MissionRunner(
                legacy, store2, workspaces_root=legacy_dir / "work",
                validation_commands=VALIDATION, semantic_retries=1,
            ).run("Create a tiny arithmetic package", legacy_dir / "project")
    finally:
        MissionRunner._one_attempt = original  # type: ignore[method-assign]

    assert store2 is not None
    mission_id = store2.list_ids()[0]
    _strip_reserve_field(legacy_dir / "state", mission_id)

    record = store2.load(mission_id)
    assert record.quality_reserve_per_task == 0
    assert record.task("arith-code").attempts[-1].status == "reviewer_blocked"
    assert quality_reserve_available(record, "arith-code") is False
    with pytest.raises(MissionLedgerError, match="used all"):
        store2.record_task_started(
            mission_id, "arith-code", baseline_sha=record.frontier_sha
        )


def test_new_mission_pins_reserve_of_one(tmp_path: Path) -> None:
    script = AttemptScript(SINGLE_PLAN, {CODE: [GOOD]})
    result, _ = _run(tmp_path, script)
    assert result.record.quality_reserve_per_task == 1


def test_explicitly_pinned_zero_survives_new_defaults(tmp_path: Path) -> None:
    store = MissionStore(tmp_path / "state")
    record = store.create(
        mission_id="mission-abcdef-01234567",
        objective="obj", plan_mission_id="p", objective_id="o",
        quality_reserve_per_task=0,
    )
    assert store.load(record.mission_id).quality_reserve_per_task == 0


def test_create_rejects_out_of_range_reserve(tmp_path: Path) -> None:
    store = MissionStore(tmp_path / "state")
    for bad in (-1, 2, 5):
        with pytest.raises(MissionLedgerError, match="quality_reserve_per_task"):
            store.create(
                mission_id="mission-abcdef-01234567",
                objective="obj", plan_mission_id="p", objective_id="o",
                quality_reserve_per_task=bad,
            )


# --- R1-R7: bounded remediation evidence keeps the real error --------------

SECRET = "sk-test-quality-reserve-credential-0123456789"


def _failing_result(stdout: str, stderr: str = "") -> TaskExecutionResult:
    return TaskExecutionResult(
        task_id="arith-code",
        specialist_id="python_backend",
        baseline_sha="",
        status="validation_failed",
        candidate_sha=None,
        validation_results=(
            ValidationResult(
                label="tests",
                command=CommandResult(
                    argv=("pytest",), cwd=".", exit_code=1, stdout=stdout,
                    stderr=stderr, duration_seconds=0.1, timed_out=False,
                ),
            ),
        ),
    )


def _record_failure(tmp_path: Path, stdout: str) -> tuple[MissionStore, str]:
    from doom_team.domain import Mission, Objective, TaskContract

    store = MissionStore(tmp_path / "state", secret_values=(SECRET,))
    record = store.create(
        mission_id="mission-abcdef-01234567",
        objective="obj", plan_mission_id="p", objective_id="o",
    )
    mission = Mission(
        id="m", objective=Objective(id="o", description="obj"),
        tasks=(TaskContract(
            id="arith-code", objective_id="o", instruction="x",
            acceptance_criteria=("y",),
        ),),
    )
    store.record_plan(record.mission_id, mission)
    store.record_task_started(record.mission_id, "arith-code", baseline_sha="")
    store.record_task_result(record.mission_id, _failing_result(stdout))
    return store, record.mission_id


def test_terminal_error_line_survives_clipping_into_remediation(
    tmp_path: Path,
) -> None:
    # R1: pytest prints the exception LAST; a >4000-char run must still hand
    # the next attempt that line.
    stdout = ("x" * 12_000) + "\nE  sqlite3.ProgrammingError: THE_REAL_ERROR\n"
    store, mission_id = _record_failure(tmp_path, stdout)
    attempt = store.load(mission_id).task("arith-code").attempts[0]
    assert "THE_REAL_ERROR" in str(attempt.validation[0]["stdout"])
    assert "THE_REAL_ERROR" in remediation_context(attempt)


def test_head_of_output_remains_available(tmp_path: Path) -> None:
    # R2: the beginning (collection errors, first failure header) survives.
    stdout = "FIRST_LINE_MARKER\n" + ("y" * 12_000) + "\nlast\n"
    store, mission_id = _record_failure(tmp_path, stdout)
    attempt = store.load(mission_id).task("arith-code").attempts[0]
    assert "FIRST_LINE_MARKER" in str(attempt.validation[0]["stdout"])


def test_stored_output_and_remediation_stay_bounded(tmp_path: Path) -> None:
    # R3 + R7: a huge run must not grow the ledger unboundedly.
    stdout = "z" * 1_000_000
    store, mission_id = _record_failure(tmp_path, stdout)
    attempt = store.load(mission_id).task("arith-code").attempts[0]
    assert len(str(attempt.validation[0]["stdout"])) <= MAX_LEDGER_OUTPUT_CHARS
    assert len(remediation_context(attempt)) <= 2_000
    ledger = (tmp_path / "state" / "missions" / f"{mission_id}.json").stat().st_size
    assert ledger < 200_000


@pytest.mark.parametrize("position", ["beginning", "end", "boundary"])
def test_secret_is_redacted_wherever_clipping_cuts(
    tmp_path: Path, position: str
) -> None:
    # R4/R5/R6: redaction happens on the WHOLE text before any clipping, so
    # a credential can never straddle a cut into survival.
    if position == "beginning":
        stdout = SECRET + ("a" * 12_000) + "\nE  boom\n"
    elif position == "end":
        stdout = ("a" * 12_000) + "\nE  boom " + SECRET + "\n"
    else:
        # Straddle the head/tail seam of the stored representation.
        head_len = MAX_LEDGER_OUTPUT_CHARS - 1_000 - 30
        stdout = ("a" * (head_len - len(SECRET) // 2)) + SECRET + ("b" * 12_000)
    store, mission_id = _record_failure(tmp_path, stdout)
    attempt = store.load(mission_id).task("arith-code").attempts[0]
    stored = str(attempt.validation[0]["stdout"])
    ledger_text = (
        tmp_path / "state" / "missions" / f"{mission_id}.json"
    ).read_text()
    # Fragments too: clip-before-redact would not leak the WHOLE secret (the
    # halves land on opposite sides of the seam), it leaks recognizable
    # PIECES. Asserting only the full string would pass under the inverted
    # order this test exists to forbid.
    for leak in (SECRET, SECRET[:16], SECRET[-16:]):
        assert leak not in stored
        assert leak not in remediation_context(attempt)
        assert leak not in ledger_text
    # And the redaction demonstrably happened rather than the secret being
    # dropped by luck: its replacement marker is present in the stored text.
    assert "[REDACTED]" in stored


def test_terminal_error_survives_even_capture_level_truncation(
    tmp_path: Path,
) -> None:
    """Output larger than the CAPTURE bound (24k chars) - not just the ledger
    bound - must still deliver its final exception line to remediation. The
    capture layer spills to a file and previously kept only the head."""
    import sys as _sys

    from doom_team.validation import ValidationResult, execute_command

    script = (
        "import sys\n"
        "sys.stdout.write('n' * 100000)\n"
        "sys.stdout.write('\\nE  RealError: CAPTURE_TAIL_MARKER\\n')\n"
        "sys.exit(1)\n"
    )
    (tmp_path / "noisy.py").write_text(script)
    command = execute_command(
        (_sys.executable, "noisy.py"),
        cwd=str(tmp_path), workspace_root=str(tmp_path), timeout=120,
    )
    assert command.stdout_truncated
    assert "CAPTURE_TAIL_MARKER" in command.stdout

    store, mission_id = _record_failure_with(
        tmp_path / "ledger", ValidationResult(label="tests", command=command)
    )
    attempt = store.load(mission_id).task("arith-code").attempts[0]
    assert "CAPTURE_TAIL_MARKER" in remediation_context(attempt)


def _record_failure_with(
    tmp_path: Path, validation: "ValidationResult"
) -> tuple[MissionStore, str]:
    from doom_team.domain import Mission, Objective, TaskContract

    store = MissionStore(tmp_path / "state", secret_values=(SECRET,))
    record = store.create(
        mission_id="mission-abcdef-01234567",
        objective="obj", plan_mission_id="p", objective_id="o",
    )
    mission = Mission(
        id="m", objective=Objective(id="o", description="obj"),
        tasks=(TaskContract(
            id="arith-code", objective_id="o", instruction="x",
            acceptance_criteria=("y",),
        ),),
    )
    store.record_plan(record.mission_id, mission)
    store.record_task_started(record.mission_id, "arith-code", baseline_sha="")
    store.record_task_result(
        record.mission_id,
        TaskExecutionResult(
            task_id="arith-code", specialist_id="python_backend",
            baseline_sha="", status="validation_failed", candidate_sha=None,
            validation_results=(validation,),
        ),
    )
    return store, record.mission_id


# --- The real-smoke-shaped regression --------------------------------------


def test_smoke_shaped_mission_converges_with_the_reserve(tmp_path: Path) -> None:
    """Structure of the failed smoke, generic content: base max 4, three
    validation failures, then a first-ever reviewer block on the final slot.
    The reserve delivers the blocker to attempt 5, which lands, and the
    mission continues past the task."""
    script = AttemptScript(
        MULTI_PLAN,
        {
            CODE: [BROKEN, BROKEN, BROKEN, REVIEW_BLOCK, GOOD],
            DOCS: [{"files": GOOD_WORK[DOCS]}],
        },
    )
    result, _ = _run(tmp_path, script, semantic_retries=3)

    assert result.status == MISSION_AWAITING_HUMAN_APPROVAL
    task = result.record.task("arith-code")
    assert [a.attempt for a in task.attempts] == [1, 2, 3, 4, 5]
    assert [a.status for a in task.attempts] == [
        "validation_failed", "validation_failed", "validation_failed",
        "reviewer_blocked", "accepted",
    ]
    # Attempt 4 is untouched history; attempt 5 consumed its feedback.
    assert task.attempts[3].reviewer is not None
    assert "missing edge case" in task.attempts[4].remediation
    assert task.integrated
    # The mission moved on: the dependent task ran and integrated.
    assert result.record.task("arith-docs").integrated
    assert result.record.all_tasks_integrated


def test_validation_only_tasks_keep_the_exact_old_maximum(tmp_path: Path) -> None:
    # No global retry increase: a task that never reaches QA/Reviewer still
    # gets exactly 1 + semantic_retries attempts.
    script = AttemptScript(SINGLE_PLAN, {CODE: [BROKEN, BROKEN, BROKEN, BROKEN, GOOD]})
    result, _ = _run(tmp_path, script, semantic_retries=3)
    assert result.status == MISSION_BLOCKED
    assert len(result.record.task("arith-code").attempts) == 4
