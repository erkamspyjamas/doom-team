"""Tests for the deliverable repository and isolated task workspaces."""

from __future__ import annotations

from pathlib import Path

import pytest

import doom_team.deliverable as deliverable_module
from doom_team.deliverable import DeliverableError, DeliverableRepo


def _repo(tmp_path: Path) -> DeliverableRepo:
    return DeliverableRepo.create(tmp_path / "project")


def test_safe_empty_destination_initializes_repo(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    assert (repo.path / ".git").is_dir()
    assert repo.is_clean()
    head = repo.head()
    assert len(head) == 40
    # Deterministic, inspectable baseline commit.
    assert repo.head() == head


def test_no_remote_side_effect(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    assert repo.remotes() == ()


def test_unsafe_destinations_rejected(tmp_path: Path) -> None:
    with pytest.raises(DeliverableError, match="too broad"):
        DeliverableRepo.create("/")
    with pytest.raises(DeliverableError, match="source tree"):
        # The running package may be installed in a wheel while these tests
        # live in an exported checkout. Protect the actual runtime source.
        DeliverableRepo.create(Path(deliverable_module.__file__).resolve().parent / "generated")
    with pytest.raises(DeliverableError, match="parent"):
        DeliverableRepo.create(tmp_path / "missing" / "nested" / "project")


def test_runtime_source_cannot_be_targeted_through_symlink(tmp_path: Path) -> None:
    runtime_source = Path(deliverable_module.__file__).resolve().parent
    alias = tmp_path / "runtime-alias"
    alias.symlink_to(runtime_source, target_is_directory=True)
    with pytest.raises(DeliverableError, match="source tree"):
        DeliverableRepo.create(alias / "generated")


def test_independent_doom_team_checkout_remains_a_valid_deliverable(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    package = repo.path / "src/doom_team"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("# independent deliverable\n")
    assert DeliverableRepo.open(repo.path).path == repo.path


def test_nonempty_destination_rejected(tmp_path: Path) -> None:
    dest = tmp_path / "occupied"
    dest.mkdir()
    (dest / "existing.txt").write_text("data")
    with pytest.raises(DeliverableError, match="not empty"):
        DeliverableRepo.create(dest)


def test_workspace_derives_from_requested_baseline(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    baseline = repo.head()
    workspace = repo.create_task_workspace("api-foundation", tmp_path / "ws")
    assert workspace.baseline_sha == baseline
    assert workspace.candidate_sha() == baseline
    assert workspace.path.is_relative_to((tmp_path / "ws").resolve())


def test_workspace_changes_do_not_mutate_deliverable(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    before = repo.head()
    workspace = repo.create_task_workspace("api-foundation", tmp_path / "ws")
    workspace.write_file("app.py", "print('hello')\n")
    candidate = workspace.commit_candidate("implement app")
    assert candidate != before
    assert repo.head() == before
    assert repo.is_clean()


def test_unsafe_task_ids_rejected(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    for bad in ("../escape", "UPPER", "a/b", "", "a" * 90, ".hidden"):
        with pytest.raises(DeliverableError, match="task id"):
            repo.create_task_workspace(bad, tmp_path / "ws")


def test_unsafe_workspace_file_paths_rejected(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    workspace = repo.create_task_workspace("task-a", tmp_path / "ws")
    for bad in ("/abs.txt", "../out.txt", "a/../../out.txt", ".git/config", "bad\x00.txt"):
        with pytest.raises(DeliverableError):
            workspace.write_file(bad, "nope")


def test_candidate_diff_and_changed_files_reflect_reality(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    workspace = repo.create_task_workspace("task-a", tmp_path / "ws")
    workspace.write_file("src/app.py", "VALUE = 1\n")
    workspace.write_file("README.md", "# Project\n")
    workspace.commit_candidate("add app and readme")
    assert set(workspace.changed_files()) == {"src/app.py", "README.md"}
    diff = workspace.diff()
    assert "VALUE = 1" in diff and "# Project" in diff
    assert workspace.is_clean()


def test_empty_candidate_commit_rejected(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    workspace = repo.create_task_workspace("task-a", tmp_path / "ws")
    with pytest.raises(DeliverableError, match="nothing to commit"):
        workspace.commit_candidate("empty")


def test_ff_only_integration_advances_deliverable(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    workspace = repo.create_task_workspace("task-a", tmp_path / "ws")
    workspace.write_file("app.py", "VALUE = 1\n")
    candidate = workspace.commit_candidate("add app")
    new_head = repo.integrate_candidate(workspace)
    assert new_head == candidate
    assert (repo.path / "app.py").read_text() == "VALUE = 1\n"
    # Serial chain: the next workspace sees the integrated baseline.
    second = repo.create_task_workspace("task-b", tmp_path / "ws")
    assert second.baseline_sha == candidate


def test_non_ff_candidate_rejected(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    stale = repo.create_task_workspace("task-a", tmp_path / "ws")
    other = repo.create_task_workspace("task-b", tmp_path / "ws")
    other.write_file("first.txt", "first\n")
    other.commit_candidate("first work")
    repo.integrate_candidate(other)
    stale.write_file("second.txt", "second\n")
    stale.commit_candidate("stale work")
    with pytest.raises(DeliverableError, match="fast-forward"):
        repo.integrate_candidate(stale)


def test_uncommitted_tracked_changes_block_integration(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    workspace = repo.create_task_workspace("task-a", tmp_path / "ws")
    workspace.write_file("app.py", "VALUE = 1\n")
    workspace.commit_candidate("add app")
    workspace.write_file("app.py", "VALUE = 2\n")
    with pytest.raises(DeliverableError, match="uncommitted tracked"):
        repo.integrate_candidate(workspace)
    # Untracked artifacts (e.g. test caches) do not block integration.
    workspace.write_file("app.py", "VALUE = 1\n")
    (workspace.path / "scratch.log").write_text("noise")
    assert repo.integrate_candidate(workspace)


def test_workspace_root_inside_deliverable_rejected(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    with pytest.raises(DeliverableError, match="inside the deliverable"):
        repo.create_task_workspace("task-a", repo.path / "ws")


def test_workspace_has_no_origin_remote(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    workspace = repo.create_task_workspace("task-a", tmp_path / "ws")
    from doom_team.deliverable import _git

    assert _git(workspace.path, "remote").strip() == ""


def test_baseline_option_injection_rejected(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    for bad in ("-f", "--detach", "-p", "HEAD", "main", "cfc159g"):
        with pytest.raises(DeliverableError, match="baseline"):
            repo.create_task_workspace("task-a", tmp_path / "ws", baseline_sha=bad)
    with pytest.raises(DeliverableError, match="not a commit|failed"):
        repo.create_task_workspace(
            "task-a", tmp_path / "ws", baseline_sha="deadbeef" * 5
        )


def test_symlink_at_target_rejected(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    workspace = repo.create_task_workspace("task-a", tmp_path / "ws")
    (workspace.path / "sneaky.txt").symlink_to(workspace.path / ".git" / "config")
    with pytest.raises(DeliverableError, match="symlink"):
        workspace.write_file("sneaky.txt", "[core] evil=true")
