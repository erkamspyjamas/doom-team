"""Tests for the deterministic capability/permission policy engine."""

from pathlib import Path

from doom_team.permissions import Capability, Decision, Role, authorize


def test_dangerous_actions_require_approval() -> None:
    for role in Role:
        for capability in (
            Capability.PUSH,
            Capability.MERGE,
            Capability.DEPLOY,
            Capability.DESTRUCTIVE_DB,
        ):
            decision = authorize(role, capability)
            assert decision.decision is Decision.REQUIRES_APPROVAL


def test_dev_can_edit_within_workspace(tmp_path: Path) -> None:
    decision = authorize(
        Role.DEV, Capability.EDIT, str(tmp_path), str(tmp_path / "module.py")
    )
    assert decision.decision is Decision.ALLOWED


def test_dev_edit_outside_workspace_denied(tmp_path: Path) -> None:
    decision = authorize(
        Role.DEV, Capability.EDIT, str(tmp_path), str(tmp_path / ".." / "outside.py")
    )
    assert decision.decision is Decision.DENIED
    assert "workspace" in decision.reason


def test_qa_cannot_edit(tmp_path: Path) -> None:
    decision = authorize(Role.QA, Capability.EDIT, str(tmp_path), str(tmp_path / "file.py"))
    assert decision.decision is Decision.DENIED


def test_reviewer_cannot_commit() -> None:
    decision = authorize(Role.REVIEWER, Capability.COMMIT, "/ws")
    assert decision.decision is Decision.DENIED


def test_supervisor_cannot_snapshot() -> None:
    decision = authorize(Role.SUPERVISOR, Capability.SNAPSHOT, "/ws")
    assert decision.decision is Decision.DENIED


def test_missing_scope_denied() -> None:
    decision = authorize(Role.DEV, Capability.EDIT, "/ws", None)
    assert decision.decision is Decision.DENIED
