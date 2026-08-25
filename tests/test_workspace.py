"""Tests for isolated Git workspace and repository control."""

from pathlib import Path

import pytest

from doom_team.evidence import InMemoryEvidenceStore
from doom_team.workspace import GitWorkspace


@pytest.fixture
def workspace_factory(tmp_path: Path) -> GitWorkspace:
    return GitWorkspace(tmp_path / "workspaces")


def test_parallel_workspaces_isolated(workspace_factory: GitWorkspace) -> None:
    ws1 = workspace_factory.create_workspace("task-1")
    ws2 = workspace_factory.create_workspace("task-1")
    patch = (
        b"diff --git a/file.txt b/file.txt\n"
        b"new file mode 100644\n"
        b"index 0000000..e69de29\n"
        b"--- /dev/null\n"
        b"+++ b/file.txt\n"
        b"@@ -0,0 +1 @@\n"
        b"+hello\n"
    )
    workspace_factory.apply_patch(ws1, patch)
    ws2_path = workspace_factory._workspace_path(ws2)
    assert not (ws2_path / "file.txt").exists()
    ws1_path = workspace_factory._workspace_path(ws1)
    assert (ws1_path / "file.txt").read_text() == "hello\n"


def test_dirty_failed_workspace_preserved(workspace_factory: GitWorkspace) -> None:
    ws = workspace_factory.create_workspace("task-1")
    patch = (
        b"diff --git a/file.txt b/file.txt\n"
        b"new file mode 100644\n"
        b"index 0000000..e69de29\n"
        b"--- /dev/null\n"
        b"+++ b/file.txt\n"
        b"@@ -0,0 +1 @@\n"
        b"+uncommitted\n"
    )
    workspace_factory.apply_patch(ws, patch)
    workspace_factory.preserve(ws)
    workspace_factory.destroy(ws)
    preserved_path = workspace_factory.base_dir / "preserved" / ws
    assert preserved_path.is_dir()
    assert (preserved_path / "file.txt").read_text() == "uncommitted\n"


def test_candidate_identity_recorded(workspace_factory: GitWorkspace) -> None:
    ws = workspace_factory.create_workspace("task-1")
    baseline = workspace_factory.baseline_commit(ws)
    patch = (
        b"diff --git a/feature.txt b/feature.txt\n"
        b"new file mode 100644\n"
        b"index 0000000..e69de29\n"
        b"--- /dev/null\n"
        b"+++ b/feature.txt\n"
        b"@@ -0,0 +1 @@\n"
        b"+work\n"
    )
    workspace_factory.apply_patch(ws, patch)
    candidate = workspace_factory.commit(ws, "feature")
    store = InMemoryEvidenceStore()
    evidence = {
        "id": "ev-1",
        "candidate_id": candidate,
        "task_id": "task-1",
        "workspace_id": ws,
        "baseline_commit": baseline,
        "candidate_commit": candidate,
    }
    store.save(evidence)
    loaded = store.load("ev-1")
    assert loaded["baseline_commit"] == baseline
    assert loaded["candidate_commit"] == candidate
    assert loaded["workspace_id"] == ws


def test_destructive_git_denied(workspace_factory: GitWorkspace) -> None:
    # The adapter must not expose destructive git operations.
    assert not hasattr(workspace_factory, "reset")
    assert not hasattr(workspace_factory, "push")
    assert not hasattr(workspace_factory, "merge")
    assert not hasattr(workspace_factory, "clean")


def test_escape_check_detects_symlink_outside(workspace_factory: GitWorkspace) -> None:
    ws = workspace_factory.create_workspace("task-1")
    ws_path = workspace_factory._workspace_path(ws)
    outside = workspace_factory.base_dir / "outside.txt"
    outside.write_text("secret")
    (ws_path / "link").symlink_to(outside)
    assert workspace_factory.escape_check(ws) is True


def test_snapshot_restore_reverts_changes(workspace_factory: GitWorkspace) -> None:
    ws = workspace_factory.create_workspace("task-1")
    snap = workspace_factory.snapshot(ws)
    patch = (
        b"diff --git a/change.txt b/change.txt\n"
        b"new file mode 100644\n"
        b"index 0000000..e69de29\n"
        b"--- /dev/null\n"
        b"+++ b/change.txt\n"
        b"@@ -0,0 +1 @@\n"
        b"+added\n"
    )
    workspace_factory.apply_patch(ws, patch)
    workspace_factory.restore(ws, snap)
    ws_path = workspace_factory._workspace_path(ws)
    assert not (ws_path / "change.txt").exists()
