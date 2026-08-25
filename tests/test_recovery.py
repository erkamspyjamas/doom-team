"""Tests for semantic recovery and loop bounds."""

from pathlib import Path

from doom_team.domain import Attempt
from doom_team.recovery import Blocker, RecoveryController


def test_runtime_retry_does_not_consume_budget(tmp_path: Path) -> None:
    controller = RecoveryController(tmp_path / "state.json")
    epoch = controller.initialize("task-1", semantic_retries=2)
    controller.record_runtime_retry(epoch.attempt)
    state = controller.state("task-1")
    assert state is not None
    assert state.semantic_retries_left == 2
    assert not controller.is_exhausted("task-1")


def test_semantic_blocker_consumes_budget(tmp_path: Path) -> None:
    controller = RecoveryController(tmp_path / "state.json")
    epoch = controller.initialize("task-1", semantic_retries=2)
    controller.on_semantic_blocker("task-1", epoch.attempt, Blocker("qa", "failed"))
    state = controller.state("task-1")
    assert state is not None
    assert state.semantic_retries_left == 1
    assert controller.is_blocked("task-1", epoch.attempt, "qa")


def test_exhaustion_is_durable_across_restart(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    controller = RecoveryController(state_path)
    epoch = controller.initialize("task-1", semantic_retries=1)
    controller.on_semantic_blocker("task-1", epoch.attempt, Blocker("reviewer", "failed"))
    assert controller.is_exhausted("task-1")

    # Simulate restart with a new controller instance reading the same file.
    controller2 = RecoveryController(state_path)
    assert controller2.is_exhausted("task-1")
    assert controller2.next_attempt("task-1") is None


def test_repeated_blocker_same_attempt_does_not_double_consume(tmp_path: Path) -> None:
    controller = RecoveryController(tmp_path / "state.json")
    epoch = controller.initialize("task-1", semantic_retries=2)
    attempt = epoch.attempt
    controller.on_semantic_blocker("task-1", attempt, Blocker("qa", "failed"))
    controller.on_semantic_blocker("task-1", attempt, Blocker("qa", "failed again"))
    state = controller.state("task-1")
    assert state is not None
    assert state.semantic_retries_left == 1


def test_duplicate_evidence_detected_and_bounded(tmp_path: Path) -> None:
    controller = RecoveryController(tmp_path / "state.json")
    controller.initialize("task-1", semantic_retries=3)
    digest = "sha256:abc"
    assert not controller.is_duplicate_evidence("task-1", digest)
    controller.record_evidence("task-1", digest)
    assert controller.is_duplicate_evidence("task-1", digest)

    # Register duplicate blocker to consume budget
    attempt = Attempt(id="task-1_a1", task_id="task-1")
    controller.record_attempt(attempt)
    controller.on_semantic_blocker("task-1", attempt, Blocker("duplicate", digest))
    state = controller.state("task-1")
    assert state is not None
    assert state.semantic_retries_left == 2


def test_no_progress_blocker_identity(tmp_path: Path) -> None:
    controller = RecoveryController(tmp_path / "state.json")
    epoch = controller.initialize("task-1", semantic_retries=2)
    controller.on_semantic_blocker("task-1", epoch.attempt, Blocker("no_progress", "no change"))
    state = controller.state("task-1")
    assert state is not None
    assert any(
        blocker.kind == "no_progress" for blocker in state.blockers.values()
    )


def test_consume_returns_false_when_exhausted(tmp_path: Path) -> None:
    controller = RecoveryController(tmp_path / "state.json")
    controller.initialize("task-1", semantic_retries=0)
    assert controller.is_exhausted("task-1")
    assert controller.consume_semantic_retry("task-1") is False


def test_next_attempt_after_exhaustion_is_none(tmp_path: Path) -> None:
    controller = RecoveryController(tmp_path / "state.json")
    epoch = controller.initialize("task-1", semantic_retries=1)
    controller.on_semantic_blocker("task-1", epoch.attempt, Blocker("qa", "fail"))
    assert controller.next_attempt("task-1") is None
