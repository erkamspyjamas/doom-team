"""Tests for durable restart/resume and crash-window reconciliation.

Process loss is simulated the honest way: the runner and the store are thrown
away and rebuilt from disk, exactly as a new process would. Where the window
matters most - between the Git fast-forward and the ledger write - the test
drives real Git on a real disposable repository.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from doom_team.approval import DurableApprovalPort
from doom_team.deliverable import DeliverableRepo, _git
from doom_team.mission import (
    MISSION_APPROVED,
    MissionLedgerError,
    MISSION_AWAITING_HUMAN_APPROVAL,
    MISSION_BLOCKED,
    MISSION_FINAL_VALIDATION_FAILED,
    MissionStore,
    approve_mission,
    attempts_remaining,
)
from doom_team.mission_runner import MissionRunError, MissionRunner
from tests.test_mission_recovery import (
    BROKEN_CODE,
    CODE,
    DOCS,
    GOOD_CODE,
    MULTI_PLAN,
    SINGLE_PLAN,
    AttemptScript,
)
from tests.test_mission_runner import STRICT_FINAL, VALIDATION

DOCS_FILES = {"README.md": "# Arithmetic\n\nInstall: none needed.\n"}


def _runner(tmp_path: Path, script: Any, *, semantic_retries: int = 1) -> MissionRunner:
    """A BRAND-NEW runner and store, as a fresh process would build them."""
    return MissionRunner(
        script,
        MissionStore(tmp_path / "state"),
        workspaces_root=tmp_path / "work",
        validation_commands=VALIDATION,
        semantic_retries=semantic_retries,
    )


def _store(tmp_path: Path) -> MissionStore:
    return MissionStore(tmp_path / "state")


class Crash(Exception):
    """Simulated process loss at a chosen boundary."""


class CrashingRunner(MissionRunner):
    """A runner that dies at a named boundary, like a killed process would."""

    crash_at: str = ""
    seen: dict[str, int]

    def __init__(self, *args: Any, crash_at: str = "", **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.crash_at = crash_at
        self.seen = {}

    def _trip(self, boundary: str) -> None:
        if self.crash_at == boundary:
            raise Crash(boundary)

    def _integrate(self, mission_id, task, repo, task_record):  # type: ignore[no-untyped-def]
        self._trip("before_integration")
        return super()._integrate(mission_id, task, repo, task_record)

    def _finalize(self, mission_id, repo):  # type: ignore[no-untyped-def]
        self._trip("before_final_validation")
        return super()._finalize(mission_id, repo)


def _multi_script() -> AttemptScript:
    return AttemptScript(
        MULTI_PLAN, {CODE: [{"files": GOOD_CODE}], DOCS: [{"files": DOCS_FILES}]}
    )


# --- 11-14, 18-20: resume classification ----------------------------------


def test_resume_of_a_completed_mission_reruns_nothing(tmp_path: Path) -> None:
    script = _multi_script()
    result = _runner(tmp_path, script).run("Build it", tmp_path / "project")
    assert result.status == MISSION_AWAITING_HUMAN_APPROVAL
    calls_before = len(script.implementer_prompts)

    resumed = _runner(tmp_path, script).resume(result.mission_id)
    assert resumed.status == MISSION_AWAITING_HUMAN_APPROVAL
    assert resumed.record.final_frontier_sha == result.record.final_frontier_sha
    # Not one further model call, and no task re-executed.
    assert len(script.implementer_prompts) == calls_before
    for task in resumed.record.tasks:
        assert len(task.attempts) == 1


def test_resume_of_an_approved_mission_is_a_terminal_no_op(tmp_path: Path) -> None:
    script = _multi_script()
    result = _runner(tmp_path, script).run("Build it", tmp_path / "project")
    approve_mission(
        _store(tmp_path), DurableApprovalPort(tmp_path / "state"), result.mission_id,
        operator="eko",
    )
    calls_before = len(script.implementer_prompts)

    resumed = _runner(tmp_path, script).resume(result.mission_id)
    assert resumed.status == MISSION_APPROVED
    assert len(script.implementer_prompts) == calls_before
    # The single human decision is untouched: still exactly one.
    assert resumed.record.approval is not None
    assert resumed.record.approval["operator"] == "eko"


def test_resume_of_a_blocked_mission_does_not_execute(tmp_path: Path) -> None:
    script = AttemptScript(SINGLE_PLAN, {CODE: [{"files": BROKEN_CODE}]})
    result = _runner(tmp_path, script).run("Build it", tmp_path / "project")
    assert result.status == MISSION_BLOCKED
    calls_before = len(script.implementer_prompts)

    resumed = _runner(tmp_path, script).resume(result.mission_id)
    assert resumed.status == MISSION_BLOCKED
    assert resumed.record.blocker["reason"] == "recovery_exhausted"
    assert len(script.implementer_prompts) == calls_before  # no automatic retry


def test_resume_of_a_failed_final_validation_does_not_execute(tmp_path: Path) -> None:
    script = _multi_script()
    runner = MissionRunner(
        script,
        _store(tmp_path),
        workspaces_root=tmp_path / "work",
        validation_commands=VALIDATION,
        final_validation_commands=STRICT_FINAL,
        semantic_retries=1,
    )
    result = runner.run("Build it", tmp_path / "project")
    assert result.status == MISSION_FINAL_VALIDATION_FAILED
    calls_before = len(script.implementer_prompts)
    resumed = _runner(tmp_path, script).resume(result.mission_id)
    assert resumed.status == MISSION_FINAL_VALIDATION_FAILED
    assert len(script.implementer_prompts) == calls_before


def test_resume_of_an_unknown_mission_fails_closed(tmp_path: Path) -> None:
    runner = _runner(tmp_path, _multi_script())
    for bad in ("mission-abcdef123456-00112233", "../escape", "nonsense"):
        with pytest.raises(MissionRunError):
            runner.resume(bad)


# --- 8: crash windows ------------------------------------------------------


def test_crash_before_first_task_resumes_from_the_baseline(tmp_path: Path) -> None:
    script = _multi_script()
    runner = CrashingRunner(
        script,
        _store(tmp_path),
        workspaces_root=tmp_path / "work",
        validation_commands=VALIDATION,
        semantic_retries=1,
        crash_at="before_integration",
    )
    with pytest.raises(Crash):
        runner.run("Build it", tmp_path / "project")
    mission_id = _store(tmp_path).list_ids()[0]

    # A new process resumes and completes the mission.
    resumed = _runner(tmp_path, _multi_script()).resume(mission_id)
    assert resumed.status == MISSION_AWAITING_HUMAN_APPROVAL
    assert all(t.integrated for t in resumed.record.tasks)


def test_crash_after_accepted_before_integration_does_not_rerun_the_task(
    tmp_path: Path,
) -> None:
    """The work was already trusted; resume integrates it instead of redoing it."""
    script = _multi_script()
    runner = CrashingRunner(
        script,
        _store(tmp_path),
        workspaces_root=tmp_path / "work",
        validation_commands=VALIDATION,
        semantic_retries=1,
        crash_at="before_integration",
    )
    with pytest.raises(Crash):
        runner.run("Build it", tmp_path / "project")

    mission_id = _store(tmp_path).list_ids()[0]
    mid = _store(tmp_path).load(mission_id)
    code = mid.task("arith-code")
    assert code.state == "accepted" and code.integrated_sha == ""
    accepted_candidate = code.candidate_sha
    calls_before = len(script.implementer_prompts)

    resumed = _runner(tmp_path, script).resume(mission_id)
    assert resumed.status == MISSION_AWAITING_HUMAN_APPROVAL
    code_after = resumed.record.task("arith-code")
    # Same single attempt, same candidate: it was integrated, not re-attempted.
    assert len(code_after.attempts) == 1
    assert code_after.candidate_sha == accepted_candidate
    assert code_after.integrated_sha == accepted_candidate
    # Only the SECOND task needed a model call on resume.
    assert len(script.implementer_prompts) == calls_before + 1


def test_crash_after_git_integration_before_ledger_reconciles_once(
    tmp_path: Path,
) -> None:
    """The most dangerous window: the commit landed, the ledger did not."""
    script = _multi_script()
    runner = CrashingRunner(
        script,
        _store(tmp_path),
        workspaces_root=tmp_path / "work",
        validation_commands=VALIDATION,
        semantic_retries=1,
        crash_at="before_integration",
    )
    with pytest.raises(Crash):
        runner.run("Build it", tmp_path / "project")

    mission_id = _store(tmp_path).list_ids()[0]
    record = _store(tmp_path).load(mission_id)
    code = record.task("arith-code")

    # Perform the real fast-forward that the crashed process was about to do,
    # then leave the ledger untouched - exactly the torn state we must survive.
    repo = DeliverableRepo.open(tmp_path / "project")
    attempt = code.attempts[-1]
    from doom_team.deliverable import TaskWorkspace

    workspace = TaskWorkspace(
        task_id="arith-code",
        path=tmp_path / "work" / attempt.workspace_dir,
        baseline_sha=attempt.baseline_sha,
        branch=attempt.branch,
    )
    integrated = repo.integrate_candidate(workspace)
    assert integrated == code.candidate_sha
    assert _store(tmp_path).load(mission_id).task("arith-code").integrated_sha == ""

    commits_before = int(_git(repo.path, "rev-list", "--count", "HEAD").strip())
    resumed = _runner(tmp_path, script).resume(mission_id)
    commits_after = int(_git(repo.path, "rev-list", "--count", "HEAD").strip())

    assert resumed.status == MISSION_AWAITING_HUMAN_APPROVAL
    code_after = resumed.record.task("arith-code")
    assert code_after.integrated_sha == integrated
    assert len(code_after.attempts) == 1
    # Exactly one commit for that candidate: the docs task added one more.
    assert commits_after == commits_before + 1
    assert _git(repo.path, "rev-list", "--count", f"{integrated}..HEAD").strip() == "1"


def test_crash_between_tasks_resumes_at_the_next_task(tmp_path: Path) -> None:
    class CrashAfterFirstIntegration(CrashingRunner):
        def _integrate(self, mission_id, task, repo, task_record):  # type: ignore[no-untyped-def]
            outcome = MissionRunner._integrate(self, mission_id, task, repo, task_record)
            if task.id == "arith-code":
                raise Crash("after_first_integration")
            return outcome

    script = _multi_script()
    runner = CrashAfterFirstIntegration(
        script,
        _store(tmp_path),
        workspaces_root=tmp_path / "work",
        validation_commands=VALIDATION,
        semantic_retries=1,
    )
    with pytest.raises(Crash):
        runner.run("Build it", tmp_path / "project")

    mission_id = _store(tmp_path).list_ids()[0]
    before = _store(tmp_path).load(mission_id)
    assert before.task("arith-code").integrated
    assert before.task("arith-docs").state == "pending"
    calls_before = len(script.implementer_prompts)

    resumed = _runner(tmp_path, script).resume(mission_id)
    assert resumed.status == MISSION_AWAITING_HUMAN_APPROVAL
    # The completed task was not touched again.
    assert len(resumed.record.task("arith-code").attempts) == 1
    assert len(script.implementer_prompts) == calls_before + 1
    assert resumed.record.task("arith-docs").baseline_sha == (
        resumed.record.task("arith-code").integrated_sha
    )


def test_crash_before_final_validation_reruns_it_fresh(tmp_path: Path) -> None:
    script = _multi_script()
    runner = CrashingRunner(
        script,
        _store(tmp_path),
        workspaces_root=tmp_path / "work",
        validation_commands=VALIDATION,
        semantic_retries=1,
        crash_at="before_final_validation",
    )
    with pytest.raises(Crash):
        runner.run("Build it", tmp_path / "project")

    mission_id = _store(tmp_path).list_ids()[0]
    record = _store(tmp_path).load(mission_id)
    assert all(t.integrated for t in record.tasks)
    assert record.final_validation is None
    calls_before = len(script.implementer_prompts)

    resumed = _runner(tmp_path, script).resume(mission_id)
    assert resumed.status == MISSION_AWAITING_HUMAN_APPROVAL
    assert resumed.record.final_validation is not None
    assert resumed.record.final_validation_passed
    assert resumed.record.final_validation_sha == resumed.record.frontier_sha
    # No task work was repeated to redo the final gate.
    assert len(script.implementer_prompts) == calls_before


# --- 15, 16: an interrupted attempt is never accepted ----------------------


def test_interrupted_attempt_is_closed_and_never_accepted(tmp_path: Path) -> None:
    class CrashInsideAttempt(MissionRunner):
        def _one_attempt(self, mission_id, task, decision, repo, accepted):  # type: ignore[no-untyped-def]
            self._store.record_task_started(
                mission_id,
                task.id,
                baseline_sha=repo.head(),
                specialist_id=decision.specialist_id,
                workspace_dir="ghost",
                branch="task/ghost",
            )
            raise Crash("inside_attempt")

    script = _multi_script()
    runner = CrashInsideAttempt(
        script,
        _store(tmp_path),
        workspaces_root=tmp_path / "work",
        validation_commands=VALIDATION,
        semantic_retries=1,
    )
    with pytest.raises(Crash):
        runner.run("Build it", tmp_path / "project")

    mission_id = _store(tmp_path).list_ids()[0]
    torn = _store(tmp_path).load(mission_id)
    assert torn.task("arith-code").open_attempt is not None  # still in flight on disk

    resumed = _runner(tmp_path, script).resume(mission_id)
    code = resumed.record.task("arith-code")
    # The interrupted attempt is terminal, never accepted, and it consumed budget.
    assert code.attempts[0].status == "interrupted"
    assert code.attempts[0].candidate_sha == ""
    assert resumed.status == MISSION_AWAITING_HUMAN_APPROVAL
    assert len(code.attempts) == 2
    assert code.attempts[1].status == "accepted"


def test_candidate_commit_alone_never_makes_a_task_accepted(tmp_path: Path) -> None:
    """A committed candidate proves nothing without validation, QA and Reviewer."""

    class CrashAfterCommit(MissionRunner):
        def _one_attempt(self, mission_id, task, decision, repo, accepted):  # type: ignore[no-untyped-def]
            frontier = repo.head()
            workspace = repo.create_task_workspace(
                task.id, self._workspaces_root, baseline_sha=frontier
            )
            workspace.write_file("arith.py", GOOD_CODE["arith.py"])
            workspace.commit_candidate("half-done work")
            self._store.record_task_started(
                mission_id,
                task.id,
                baseline_sha=frontier,
                specialist_id=decision.specialist_id,
                workspace_dir=workspace.path.name,
                branch=workspace.branch,
            )
            raise Crash("after_commit")

    script = _multi_script()
    runner = CrashAfterCommit(
        script,
        _store(tmp_path),
        workspaces_root=tmp_path / "work",
        validation_commands=VALIDATION,
        semantic_retries=1,
    )
    with pytest.raises(Crash):
        runner.run("Build it", tmp_path / "project")
    mission_id = _store(tmp_path).list_ids()[0]

    resumed = _runner(tmp_path, script).resume(mission_id)
    code = resumed.record.task("arith-code")
    assert code.attempts[0].status == "interrupted"
    assert code.attempts[0].candidate_sha == ""  # the orphan commit is not a candidate
    assert code.integrated_sha != ""  # a real attempt 2 produced the integrated work
    assert code.attempts[1].status == "accepted"


# --- 6: restart never refills the budget -----------------------------------


def test_restart_does_not_reset_the_recovery_budget(tmp_path: Path) -> None:
    """A crash after a failed attempt leaves exactly one attempt, not two."""
    script = AttemptScript(SINGLE_PLAN, {CODE: [{"files": BROKEN_CODE}]})

    class CrashAfterFailure(MissionRunner):
        def _one_attempt(self, mission_id, task, decision, repo, accepted):  # type: ignore[no-untyped-def]
            MissionRunner._one_attempt(self, mission_id, task, decision, repo, accepted)
            raise Crash("after_failed_attempt")

    runner = CrashAfterFailure(
        script,
        _store(tmp_path),
        workspaces_root=tmp_path / "work",
        validation_commands=VALIDATION,
        semantic_retries=1,
    )
    with pytest.raises(Crash):
        runner.run("Build it", tmp_path / "project")

    mission_id = _store(tmp_path).list_ids()[0]
    after_crash = _store(tmp_path).load(mission_id)
    assert len(after_crash.task("arith-code").attempts) == 1
    assert attempts_remaining(after_crash, "arith-code") == 1

    resumed = _runner(tmp_path, script).resume(mission_id)
    # Exactly one more attempt was available, then exhaustion. Never a third.
    assert resumed.status == MISSION_BLOCKED
    assert resumed.record.blocker["reason"] == "recovery_exhausted"
    assert len(resumed.record.task("arith-code").attempts) == 2
    assert attempts_remaining(resumed.record, "arith-code") == 0
    assert len(script.implementer_prompts) == 2


# --- 23, 24: provenance must be unambiguous --------------------------------


def test_unexplained_frontier_movement_fails_closed_on_resume(tmp_path: Path) -> None:
    script = _multi_script()
    runner = CrashingRunner(
        script,
        _store(tmp_path),
        workspaces_root=tmp_path / "work",
        validation_commands=VALIDATION,
        semantic_retries=1,
        crash_at="before_final_validation",
    )
    with pytest.raises(Crash):
        runner.run("Build it", tmp_path / "project")
    mission_id = _store(tmp_path).list_ids()[0]

    # A stranger commits into the deliverable while the mission is down.
    repo_path = tmp_path / "project"
    (repo_path / "stranger.txt").write_text("not ours\n")
    _git(repo_path, "add", "-A")
    _git(repo_path, "commit", "-q", "-m", "out-of-band")

    resumed = _runner(tmp_path, script).resume(mission_id)
    assert resumed.status == MISSION_BLOCKED
    assert resumed.record.blocker["reason"] == "frontier_ambiguous"
    assert resumed.record.final_validation is None


def test_missing_deliverable_on_resume_fails_closed(tmp_path: Path) -> None:
    import shutil

    script = _multi_script()
    runner = CrashingRunner(
        script,
        _store(tmp_path),
        workspaces_root=tmp_path / "work",
        validation_commands=VALIDATION,
        semantic_retries=1,
        crash_at="before_final_validation",
    )
    with pytest.raises(Crash):
        runner.run("Build it", tmp_path / "project")
    mission_id = _store(tmp_path).list_ids()[0]
    shutil.rmtree(tmp_path / "project")

    resumed = _runner(tmp_path, script).resume(mission_id)
    assert resumed.status == MISSION_BLOCKED
    assert resumed.record.blocker["reason"] == "deliverable_unavailable"


# --- 12: durability through an entirely fresh reader -----------------------


def test_resumed_mission_state_is_visible_to_a_new_reader(tmp_path: Path) -> None:
    script = AttemptScript(
        MULTI_PLAN,
        {CODE: [{"files": BROKEN_CODE}, {"files": GOOD_CODE}], DOCS: [{"files": DOCS_FILES}]},
    )
    runner = CrashingRunner(
        script,
        _store(tmp_path),
        workspaces_root=tmp_path / "work",
        validation_commands=VALIDATION,
        semantic_retries=1,
        crash_at="before_final_validation",
    )
    with pytest.raises(Crash):
        runner.run("Build it", tmp_path / "project")
    mission_id = _store(tmp_path).list_ids()[0]
    resumed = _runner(tmp_path, script).resume(mission_id)
    assert resumed.status == MISSION_AWAITING_HUMAN_APPROVAL

    fresh = MissionStore(tmp_path / "state").load(mission_id)
    code = fresh.task("arith-code")
    assert [a.status for a in code.attempts] == ["validation_failed", "accepted"]
    assert fresh.max_attempts_per_task == 2
    assert attempts_remaining(fresh, "arith-code") == 0
    assert fresh.final_validation_passed
    assert fresh.final_validation_sha == fresh.frontier_sha
    assert fresh.status == MISSION_AWAITING_HUMAN_APPROVAL

    # And the approval gate still works after a resumed mission.
    approved = approve_mission(
        MissionStore(tmp_path / "state"),
        DurableApprovalPort(tmp_path / "state"),
        mission_id,
        operator="eko",
    )
    assert approved.status == MISSION_APPROVED


# --- Integrity-review regressions ------------------------------------------


def test_reconcile_never_adopts_an_out_of_band_commit_as_the_frontier(
    tmp_path: Path,
) -> None:
    """A stranger's commit stacked on a torn integration must not be absorbed."""
    from doom_team.deliverable import TaskWorkspace

    script = _multi_script()
    runner = CrashingRunner(
        script,
        _store(tmp_path),
        workspaces_root=tmp_path / "work",
        validation_commands=VALIDATION,
        semantic_retries=1,
        crash_at="before_integration",
    )
    with pytest.raises(Crash):
        runner.run("Build it", tmp_path / "project")

    mission_id = _store(tmp_path).list_ids()[0]
    code = _store(tmp_path).load(mission_id).task("arith-code")
    attempt = code.attempts[-1]
    repo = DeliverableRepo.open(tmp_path / "project")

    # The torn state: the fast-forward happened, the ledger write did not.
    workspace = TaskWorkspace(
        task_id="arith-code",
        path=tmp_path / "work" / attempt.workspace_dir,
        baseline_sha=attempt.baseline_sha,
        branch=attempt.branch,
    )
    repo.integrate_candidate(workspace)
    # And while the mission was down, an unreviewed commit landed on top.
    (repo.path / "stranger.txt").write_text("never reviewed\n")
    _git(repo.path, "add", "-A")
    _git(repo.path, "commit", "-q", "-m", "out-of-band")
    stranger_head = repo.head()

    resumed = _runner(tmp_path, script).resume(mission_id)

    assert resumed.status == MISSION_BLOCKED
    assert resumed.record.blocker["reason"] == "frontier_ambiguous"
    # The frontier is the reviewed candidate, never the stranger's commit.
    assert resumed.record.task("arith-code").integrated_sha == code.candidate_sha
    assert resumed.record.frontier_sha != stranger_head
    assert resumed.record.final_validation is None
    assert not resumed.approval_required


def test_moved_workspace_branch_cannot_smuggle_unreviewed_commits(
    tmp_path: Path,
) -> None:
    """Only the exact reviewed candidate may be integrated."""
    script = _multi_script()
    runner = CrashingRunner(
        script,
        _store(tmp_path),
        workspaces_root=tmp_path / "work",
        validation_commands=VALIDATION,
        semantic_retries=1,
        crash_at="before_integration",
    )
    with pytest.raises(Crash):
        runner.run("Build it", tmp_path / "project")

    mission_id = _store(tmp_path).list_ids()[0]
    attempt = _store(tmp_path).load(mission_id).task("arith-code").attempts[-1]
    # The preserved workspace gains an extra commit nobody reviewed.
    ws_path = tmp_path / "work" / attempt.workspace_dir
    (ws_path / "sneaky.py").write_text("SNEAKED = True\n")
    _git(ws_path, "add", "-A")
    _git(ws_path, "commit", "-q", "-m", "unreviewed extra")

    resumed = _runner(tmp_path, script).resume(mission_id)
    assert resumed.status == MISSION_BLOCKED
    assert resumed.record.blocker["reason"] == "candidate_mismatch"
    repo = DeliverableRepo.open(tmp_path / "project")
    assert not (repo.path / "sneaky.py").exists()


def test_a_second_engine_cannot_execute_the_same_mission(tmp_path: Path) -> None:
    """Mission execution is exclusive; a concurrent resume fails closed."""
    import threading

    script = _multi_script()
    started = threading.Event()
    release = threading.Event()

    class SlowRunner(MissionRunner):
        def _one_attempt(self, mission_id, task, decision, repo, accepted):  # type: ignore[no-untyped-def]
            started.set()
            release.wait(timeout=10)
            return MissionRunner._one_attempt(
                self, mission_id, task, decision, repo, accepted
            )

    slow = SlowRunner(
        script,
        _store(tmp_path),
        workspaces_root=tmp_path / "work",
        validation_commands=VALIDATION,
        semantic_retries=1,
    )
    outcome: dict[str, Any] = {}

    def first() -> None:
        outcome["first"] = slow.run("Build it", tmp_path / "project")

    thread = threading.Thread(target=first)
    thread.start()
    assert started.wait(timeout=10)

    mission_id = _store(tmp_path).list_ids()[0]
    # The intruder is refused outright. It must NOT write a blocker: the
    # mission is healthy and someone else is running it.
    with pytest.raises(MissionLedgerError, match="already being executed"):
        _runner(tmp_path, _multi_script()).resume(mission_id)
    assert _store(tmp_path).load(mission_id).blocker is None
    release.set()
    thread.join(timeout=60)

    assert outcome["first"].status == MISSION_AWAITING_HUMAN_APPROVAL
    # The live attempt was never stolen or marked interrupted.
    final = MissionStore(tmp_path / "state").load(mission_id)
    for task in final.tasks:
        assert [a.status for a in task.attempts] == ["accepted"]


def test_a_record_written_before_the_bound_existed_still_runs(tmp_path: Path) -> None:
    """A pre-T6 ledger adopts the canonical bound instead of reading zero."""
    from doom_team.durable import atomic_write_json, read_json_checked

    script = _multi_script()
    runner = CrashingRunner(
        script,
        _store(tmp_path),
        workspaces_root=tmp_path / "work",
        validation_commands=VALIDATION,
        semantic_retries=1,
        crash_at="before_final_validation",
    )
    with pytest.raises(Crash):
        runner.run("Build it", tmp_path / "project")
    mission_id = _store(tmp_path).list_ids()[0]

    # Strip the T6-only fields, exactly as a T5-written record would look.
    path = tmp_path / "state" / "missions" / f"{mission_id}.json"
    payload = read_json_checked(path)
    payload.pop("max_attempts_per_task", None)
    for task in payload["tasks"]:
        task.pop("attempts", None)
    atomic_write_json(path, payload)

    record = MissionStore(tmp_path / "state").load(mission_id)
    from doom_team.mission import DEFAULT_MAX_ATTEMPTS_PER_TASK

    # The canonical default, not zero - a pre-T6 mission is still runnable.
    assert record.max_attempts_per_task == DEFAULT_MAX_ATTEMPTS_PER_TASK
    resumed = _runner(tmp_path, script).resume(mission_id)
    assert resumed.status == MISSION_AWAITING_HUMAN_APPROVAL
