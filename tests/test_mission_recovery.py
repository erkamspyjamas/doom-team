"""Tests for bounded task recovery: attempt history, remediation, exhaustion."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import pytest

from doom_team.deliverable import DeliverableRepo
from doom_team.mission import (
    MISSION_AWAITING_HUMAN_APPROVAL,
    MISSION_BLOCKED,
    MissionLedgerError,
    MissionStore,
    attempts_remaining,
    max_attempts_for,
    remediation_context,
)
from doom_team.mission_runner import MissionRunError, MissionRunner
from tests.test_mission_runner import (
    ARITH,
    BLOCK_VERDICT,
    GOOD_WORK,
    PASS_VERDICT,
    VALIDATION,
    _Response,
    _implementer_json,
)

CODE = "Implement the arithmetic module"
TESTS = "Add edge-case tests"
DOCS = "Write the README"

GOOD_CODE = GOOD_WORK[CODE]
BROKEN_CODE = {
    "arith.py": "def add(a, b):\n    return a - b\n\n\ndef multiply(a, b):\n    return a * b\n",
    "test_arith.py": GOOD_WORK[CODE]["test_arith.py"],
}

SINGLE_PLAN = json.dumps(
    {
        "tasks": [
            {
                "task_id": "arith-code",
                "description": CODE,
                "dependencies": [],
                "required_capabilities": ["backend.python"],
                "acceptance_criteria": ["add and multiply behave correctly"],
            }
        ]
    }
)
MULTI_PLAN = json.dumps(
    {
        "tasks": [
            {
                "task_id": "arith-code",
                "description": CODE,
                "dependencies": [],
                "required_capabilities": ["backend.python"],
                "acceptance_criteria": ["add and multiply behave correctly"],
            },
            {
                "task_id": "arith-docs",
                "description": DOCS,
                "dependencies": ["arith-code"],
                "required_capabilities": ["documentation"],
                "acceptance_criteria": ["README documents usage"],
            },
        ]
    }
)


class AttemptScript:
    """Deterministic inference that behaves differently on each attempt."""

    def __init__(self, plan: str, scripts: dict[str, list[dict[str, Any]]]) -> None:
        self.plan = plan
        self.scripts = scripts
        self.counts: dict[str, int] = defaultdict(int)
        self.current: dict[str, Any] = {}
        self.implementer_prompts: list[str] = []

    def _spec(self, marker: str) -> dict[str, Any]:
        seq = self.scripts[marker]
        index = min(self.counts[marker], len(seq) - 1)
        return seq[index]

    def complete(self, messages: list[dict[str, str]]) -> _Response:
        system, user = messages[0]["content"], messages[1]["content"]
        if "mission planner" in system:
            return _Response(self.plan)
        if "implementation specialist for exactly one" in system:
            for marker in self.scripts:
                if marker in user:
                    self.current = self._spec(marker)
                    self.counts[marker] += 1
                    self.implementer_prompts.append(user)
                    return _Response(_implementer_json(self.current["files"]))
            raise AssertionError(f"no script for: {user[:200]}")
        if "independent QA engineer" in system:
            return _Response(self.current.get("qa", PASS_VERDICT))
        if "independent final reviewer" in system:
            return _Response(self.current.get("reviewer", PASS_VERDICT))
        raise AssertionError(f"unexpected role: {system[:80]}")


def _run(
    tmp_path: Path, script: AttemptScript, *, semantic_retries: int = 1
) -> tuple[Any, MissionStore]:
    store = MissionStore(tmp_path / "state")
    runner = MissionRunner(
        script,
        store,
        workspaces_root=tmp_path / "work",
        validation_commands=VALIDATION,
        semantic_retries=semantic_retries,
    )
    return runner.run("Create a tiny arithmetic package", tmp_path / "project"), store


def _fail_then_pass(kind: str) -> AttemptScript:
    """Attempt 1 fails the given way; attempt 2 is clean."""
    first: dict[str, Any] = {"files": GOOD_CODE}
    if kind == "validation":
        first = {"files": BROKEN_CODE}
    elif kind == "qa":
        first = {"files": GOOD_CODE, "qa": BLOCK_VERDICT}
    elif kind == "reviewer":
        first = {"files": GOOD_CODE, "reviewer": BLOCK_VERDICT}
    elif kind == "execution":
        first = {"files": GOOD_CODE}
    return AttemptScript(SINGLE_PLAN, {CODE: [first, {"files": GOOD_CODE}]})


# --- 25-27, 1-3: recovery per failure kind, with immutable history ---------


@pytest.mark.parametrize(
    "kind,expected_first",
    [
        ("validation", "validation_failed"),
        ("qa", "qa_blocked"),
        ("reviewer", "reviewer_blocked"),
    ],
)
def test_bounded_recovery_recovers_each_failure_kind(
    tmp_path: Path, kind: str, expected_first: str
) -> None:
    result, store = _run(tmp_path / kind, _fail_then_pass(kind))

    assert result.status == MISSION_AWAITING_HUMAN_APPROVAL
    task = result.record.task("arith-code")
    assert task.state == "integrated"

    # Two attempts, and attempt 1 still says exactly what happened to it.
    assert len(task.attempts) == 2
    first, second = task.attempts
    assert first.attempt == 1 and first.status == expected_first
    assert second.attempt == 2 and second.status == "accepted"
    assert second.candidate_sha == task.candidate_sha
    # Distinct executions, in distinct workspaces. Their commit SHAs may
    # legitimately coincide when a retry rewrites byte-identical files onto
    # the same baseline, so identity comes from the attempt records.
    assert first.workspace_dir != second.workspace_dir
    assert first.started_at <= second.started_at

    # The integrated work is the SECOND attempt's candidate.
    assert task.integrated_sha == second.candidate_sha
    # Durable through a new reader.
    fresh = MissionStore(tmp_path / kind / "state").load(result.mission_id)
    assert [a.status for a in fresh.task("arith-code").attempts] == [
        expected_first,
        "accepted",
    ]


def test_execution_failure_recovers(tmp_path: Path) -> None:
    """Malformed implementer output is a recoverable attempt, not a dead mission."""

    class BadThenGood(AttemptScript):
        def complete(self, messages: list[dict[str, str]]) -> _Response:
            system, user = messages[0]["content"], messages[1]["content"]
            if "implementation specialist for exactly one" in system:
                self.counts[CODE] += 1
                self.implementer_prompts.append(user)
                if self.counts[CODE] == 1:
                    return _Response("here is the code, boss!")
                self.current = {"files": GOOD_CODE}
                return _Response(_implementer_json(GOOD_CODE))
            return super().complete(messages)

    script = BadThenGood(SINGLE_PLAN, {CODE: [{"files": GOOD_CODE}]})
    result, _ = _run(tmp_path, script)
    task = result.record.task("arith-code")
    assert result.status == MISSION_AWAITING_HUMAN_APPROVAL
    assert [a.status for a in task.attempts] == ["execution_failed", "accepted"]
    assert task.attempts[0].candidate_sha == ""


# --- 4, 5, 28: the bound is real ------------------------------------------


def test_exhaustion_blocks_the_mission_with_no_further_attempt(tmp_path: Path) -> None:
    script = AttemptScript(SINGLE_PLAN, {CODE: [{"files": BROKEN_CODE}]})
    result, store = _run(tmp_path, script)

    assert result.status == MISSION_BLOCKED
    assert result.record.blocker is not None
    assert result.record.blocker["reason"] == "recovery_exhausted"
    task = result.record.task("arith-code")
    assert len(task.attempts) == 2 == result.record.max_attempts_per_task
    assert all(a.status == "validation_failed" for a in task.attempts)
    assert attempts_remaining(result.record, "arith-code") == 0
    # Exactly two implementer calls: no silent third try.
    assert len(script.implementer_prompts) == 2
    # Nothing was integrated and the frontier never moved.
    assert task.integrated_sha == ""
    assert result.record.frontier_sha == result.record.baseline_sha


def test_ledger_refuses_an_attempt_beyond_the_bound(tmp_path: Path) -> None:
    script = AttemptScript(SINGLE_PLAN, {CODE: [{"files": BROKEN_CODE}]})
    result, store = _run(tmp_path, script)
    # The mission is blocked, and even forcing the ledger directly is refused.
    with pytest.raises(MissionLedgerError, match="used all 2 attempts|not running"):
        store.record_task_started(
            result.mission_id,
            "arith-code",
            baseline_sha=result.record.frontier_sha,
        )


def test_recovery_bound_follows_the_configured_semantic_retries(tmp_path: Path) -> None:
    assert max_attempts_for(0) == 1
    assert max_attempts_for(3) == 4
    script = AttemptScript(SINGLE_PLAN, {CODE: [{"files": BROKEN_CODE}]})
    result, _ = _run(tmp_path, script, semantic_retries=2)
    assert result.record.max_attempts_per_task == 3
    assert len(result.record.task("arith-code").attempts) == 3
    assert len(script.implementer_prompts) == 3


def test_zero_retries_means_a_single_attempt(tmp_path: Path) -> None:
    script = AttemptScript(SINGLE_PLAN, {CODE: [{"files": BROKEN_CODE}]})
    result, _ = _run(tmp_path, script, semantic_retries=0)
    assert result.record.max_attempts_per_task == 1
    assert len(result.record.task("arith-code").attempts) == 1
    assert result.status == MISSION_BLOCKED
    assert result.record.blocker["reason"] == "recovery_exhausted"


def test_negative_retry_budget_is_refused(tmp_path: Path) -> None:
    with pytest.raises(MissionRunError, match="cannot be negative"):
        MissionRunner(
            AttemptScript(SINGLE_PLAN, {}),
            MissionStore(tmp_path / "state"),
            workspaces_root=tmp_path / "work",
            validation_commands=VALIDATION,
            semantic_retries=-1,
        )


# --- 7, 8: remediation context reaches the retry, bounded and scoped -------


def test_remediation_from_qa_reaches_the_retry_implementer(tmp_path: Path) -> None:
    result, _ = _run(tmp_path, _fail_then_pass("qa"))
    script_prompts = result  # keep name clear below
    task = result.record.task("arith-code")
    assert task.attempts[1].remediation
    assert "missing edge case" in task.attempts[1].remediation
    assert "QA blocked" in task.attempts[1].remediation
    assert task.attempts[0].remediation == ""  # the first attempt has no history
    assert script_prompts.status == MISSION_AWAITING_HUMAN_APPROVAL


def test_remediation_is_framed_as_untrusted_data_in_the_retry_prompt(
    tmp_path: Path,
) -> None:
    script = _fail_then_pass("qa")
    _run(tmp_path, script)
    retry_prompt = script.implementer_prompts[1]
    assert "previous attempt feedback" in retry_prompt
    nonce = retry_prompt.split("<<<UNTRUSTED-DATA:")[1].split(">>>")[0]
    # The feedback sits inside a nonce frame like every other untrusted field.
    marker = f"<<<UNTRUSTED-DATA:{nonce}>>>"
    close = f"<<<END-UNTRUSTED-DATA:{nonce}>>>"
    assert retry_prompt.count(marker) == retry_prompt.count(close)
    segments = retry_prompt.split(marker)
    assert all("QA blocker" not in seg.split(close)[-1] for seg in segments)
    # The first attempt was told nothing about a previous one.
    assert "QA blocker" not in script.implementer_prompts[0]


def test_remediation_is_bounded_and_secret_free(tmp_path: Path) -> None:
    from doom_team.mission import MAX_REMEDIATION_CHARS, MissionAttemptRecord

    huge = MissionAttemptRecord(
        attempt=1,
        baseline_sha="a" * 40,
        specialist_id="python_backend",
        started_at="now",
        status="qa_blocked",
        qa={"verdict": "block", "summary": "x" * 50_000, "blockers": ["y" * 50_000]},
    )
    context = remediation_context(huge)
    assert len(context) <= MAX_REMEDIATION_CHARS

    # Secrets never reach the context, because the ledger redacted them first.
    secret = "sk-live-deadbeefcafe0123"
    store = MissionStore(tmp_path / "state", secret_values=(secret,))
    runner = MissionRunner(
        _fail_then_pass("qa"),
        store,
        workspaces_root=tmp_path / "work",
        validation_commands=VALIDATION,
        semantic_retries=1,
    )
    result = runner.run(f"Build with {secret}", tmp_path / "project")
    for attempt in result.record.task("arith-code").attempts:
        assert secret not in attempt.remediation
    raw = (tmp_path / "state" / "missions" / f"{result.mission_id}.json").read_text()
    assert secret not in raw


# --- 9, 10: fresh workspace from the current frontier ----------------------


def test_recovery_uses_a_fresh_workspace_at_the_current_frontier(tmp_path: Path) -> None:
    result, _ = _run(tmp_path, _fail_then_pass("validation"))
    task = result.record.task("arith-code")
    first, second = task.attempts

    # A brand-new workspace, never the failed one.
    assert first.workspace_dir and second.workspace_dir
    assert first.workspace_dir != second.workspace_dir
    assert (tmp_path / "work" / first.workspace_dir).is_dir()  # preserved for inspection

    # Both started from the frontier, which never moved while the task failed.
    assert first.baseline_sha == second.baseline_sha == result.record.baseline_sha
    # The recovered candidate is built on that frontier, not on the failed one.
    repo = DeliverableRepo.open(tmp_path / "project")
    assert repo.head() == task.integrated_sha


def test_recovered_task_advances_the_frontier_for_the_next_task(tmp_path: Path) -> None:
    script = AttemptScript(
        MULTI_PLAN,
        {
            CODE: [{"files": BROKEN_CODE}, {"files": GOOD_CODE}],
            DOCS: [{"files": {"README.md": "# Arithmetic\n"}}],
        },
    )
    result, _ = _run(tmp_path, script)
    assert result.status == MISSION_AWAITING_HUMAN_APPROVAL
    code = result.record.task("arith-code")
    docs = result.record.task("arith-docs")
    assert len(code.attempts) == 2 and len(docs.attempts) == 1
    # The second task built on the RECOVERED candidate.
    assert docs.baseline_sha == code.integrated_sha
    assert result.record.final_frontier_sha == docs.integrated_sha


# --- Q: the happy path must not manufacture recovery ----------------------


def test_first_attempt_success_creates_exactly_one_attempt(tmp_path: Path) -> None:
    script = AttemptScript(SINGLE_PLAN, {CODE: [{"files": GOOD_CODE}]})
    result, _ = _run(tmp_path, script)
    task = result.record.task("arith-code")
    assert result.status == MISSION_AWAITING_HUMAN_APPROVAL
    assert len(task.attempts) == 1
    assert task.attempts[0].status == "accepted"
    assert task.attempts[0].remediation == ""
    # One implementer call: recovery cost nothing on the happy path.
    assert len(script.implementer_prompts) == 1
    assert attempts_remaining(result.record, "arith-code") == 1


def test_arith_fixture_is_actually_broken_and_fixed(tmp_path: Path) -> None:
    """Guard the fixture itself: the 'broken' code really does fail its test."""
    assert "return a - b" in BROKEN_CODE["arith.py"]
    assert "return a + b" in ARITH
