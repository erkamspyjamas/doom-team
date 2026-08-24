"""Tests for the DEV -> QA -> Reviewer lifecycle."""

import datetime
from pathlib import Path

import pytest

from doom_team.domain import Attempt, Candidate, Evidence
from doom_team.lifecycle import Lifecycle
from doom_team.verdicts import DurableVerdictStore, InMemoryVerdictStore
from doom_team.workspace import GitWorkspace


def _make_candidate_and_evidence(ws: GitWorkspace, task_id: str = "task-1") -> tuple[Candidate, Evidence]:
    workspace_id = ws.create_workspace(task_id)
    patch = b"diff --git a/file.txt b/file.txt\nnew file mode 100644\nindex 0000000..e69de29\n--- /dev/null\n+++ b/file.txt\n@@ -0,0 +1 @@\n+content\n"
    ws.apply_patch(workspace_id, patch)
    candidate_commit = ws.commit(workspace_id, "candidate")
    baseline_commit = ws.baseline_commit(workspace_id)

    attempt = Attempt(id="attempt-1", task_id=task_id)
    candidate = Candidate(
        id="cand-1",
        task_id=task_id,
        attempt=attempt,
        baseline_commit=baseline_commit,
        candidate_commit=candidate_commit,
        workspace_id=workspace_id,
    )
    evidence = Evidence(
        id="ev-1",
        candidate=candidate,
        objective_id="obj-1",
        mission_id="mission-1",
        producer_role="dev",
        producer_run="run-1",
        command="make test",
        artifacts=("log.txt",),
        actual_model="deepseek-v4",
        actual_runtime="local",
        timestamp=datetime.datetime.now(datetime.timezone.utc),
        digest="sha256:abc",
    )
    return candidate, evidence


def test_lifecycle_runs_qa_and_reviewer_and_persists_verdicts(tmp_path: Path) -> None:
    ws = GitWorkspace(tmp_path / "workspaces")
    candidate, evidence = _make_candidate_and_evidence(ws)

    verdict_store = DurableVerdictStore(tmp_path / "verdicts")
    lifecycle = Lifecycle(verdict_store)

    def qa_checker(cand: Candidate, ev: Evidence) -> bool:
        # read-only check: verify the file exists
        ws_path = ws._workspace_path(cand.workspace_id)
        return (ws_path / "file.txt").exists()

    def reviewer_checker(cand: Candidate, ev: Evidence) -> bool:
        return True

    qa_verdict, reviewer_verdict = lifecycle.run(candidate, evidence, qa_checker, reviewer_checker)

    assert qa_verdict.decision is True
    assert reviewer_verdict.decision is True
    assert qa_verdict.role == "QA"
    assert reviewer_verdict.role == "REVIEWER"

    # Verify durable persistence
    verdict_files = list((tmp_path / "verdicts" / "verdicts").glob("*.json"))
    assert len(verdict_files) == 2


def test_lifecycle_rejects_stale_evidence(tmp_path: Path) -> None:
    ws = GitWorkspace(tmp_path / "workspaces")
    candidate, evidence = _make_candidate_and_evidence(ws)

    # Create a second candidate with a different commit to make evidence stale
    other_ws = GitWorkspace(tmp_path / "other_workspaces")
    other_candidate, _ = _make_candidate_and_evidence(other_ws, task_id="task-2")
    stale_evidence = Evidence(
        id="ev-stale",
        candidate=other_candidate,
        objective_id="obj-1",
        mission_id="mission-1",
        producer_role="dev",
        producer_run="run-2",
        command="make test",
        artifacts=(),
        actual_model="deepseek-v4",
        actual_runtime="local",
        timestamp=datetime.datetime.now(datetime.timezone.utc),
        digest="sha256:def",
    )

    verdict_store = InMemoryVerdictStore()
    lifecycle = Lifecycle(verdict_store)

    with pytest.raises(ValueError, match="Evidence is not bound"):
        lifecycle.run(
            candidate,
            stale_evidence,
            lambda c, e: True,
            lambda c, e: True,
        )


def test_lifecycle_is_read_only_for_qa_and_reviewer(tmp_path: Path) -> None:
    ws = GitWorkspace(tmp_path / "workspaces")
    candidate, evidence = _make_candidate_and_evidence(ws)

    # Snapshot the workspace state before running the lifecycle
    before = ws.snapshot(candidate.workspace_id)

    verdict_store = InMemoryVerdictStore()
    lifecycle = Lifecycle(verdict_store)

    def qa_checker(cand: Candidate, ev: Evidence) -> bool:
        # read-only check: ensure the workspace is unchanged
        ws_path = ws._workspace_path(cand.workspace_id)
        return (ws_path / "file.txt").read_text() == "content\n"

    def reviewer_checker(cand: Candidate, ev: Evidence) -> bool:
        return True

    lifecycle.run(candidate, evidence, qa_checker, reviewer_checker)

    # Verify the workspace was not mutated
    after = ws.snapshot(candidate.workspace_id)
    assert after == before
