"""Isolated Git-backed workspace and repository control.

Implements the WorkspacePort and RepositoryPort protocols using a local
Git repository per workspace. Workspaces are fully independent, snapshots
are reversible copies, and failed/dirty workspaces can be preserved for
forensic retention instead of being destroyed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from pathlib import Path

from doom_team.ports import RepositoryPort, WorkspacePort


class GitWorkspace(WorkspacePort, RepositoryPort):
    """A Git-backed workspace that is isolated, reversible and safe.

    Each workspace is a directory under a base directory with its own Git
    repository. Snapshots are full copies of the working tree. Failed or
    dirty workspaces can be preserved instead of deleted.
    """

    def __init__(self, base_dir: str | Path) -> None:
        self.base_dir = Path(base_dir).resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._preserved: set[str] = set()

    def _workspace_path(self, workspace_id: str) -> Path:
        path = (self.base_dir / workspace_id).resolve()
        if not path.is_relative_to(self.base_dir):
            raise ValueError(f"Workspace {workspace_id} escapes base directory")
        return path

    def create_workspace(self, task_id: str) -> str:
        """Create an isolated Git repo for a task and return its id."""
        workspace_id = f"{task_id}_{uuid.uuid4().hex[:8]}"
        path = self._workspace_path(workspace_id)
        path.mkdir(parents=True, exist_ok=False)
        self._git(path, "init")
        self._git(path, "config", "user.name", "Doom Autopilot")
        self._git(path, "config", "user.email", "autopilot@doom.team")
        (path / ".gitignore").write_text("# Doom workspace\nbaseline.txt\n")
        self._git(path, "add", ".gitignore")
        self._git(path, "commit", "-m", "baseline", "--allow-empty")
        baseline = self._git(path, "rev-parse", "HEAD").strip()
        (path / "baseline.txt").write_text(baseline + "\n")
        return workspace_id

    def snapshot(self, workspace_id: str) -> str:
        """Capture a reversible snapshot of the working tree."""
        path = self._workspace_path(workspace_id)
        snapshot_id = f"{workspace_id}_snap_{uuid.uuid4().hex[:8]}"
        snap_path = self.base_dir / snapshot_id
        shutil.copytree(path, snap_path, ignore=shutil.ignore_patterns(".git"))
        return snapshot_id

    def restore(self, workspace_id: str, snapshot_id: str) -> None:
        """Restore a workspace to a previous snapshot."""
        path = self._workspace_path(workspace_id)
        snap_path = self._workspace_path(snapshot_id)
        if not snap_path.is_dir():
            raise FileNotFoundError(f"Snapshot {snapshot_id} not found")
        for item in path.iterdir():
            if item.name != ".git":
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()
        for item in snap_path.iterdir():
            shutil.copy2(item, path / item.name)

    def destroy(self, workspace_id: str) -> None:
        """Delete a workspace unless it has been preserved."""
        if workspace_id in self._preserved:
            return
        path = self._workspace_path(workspace_id)
        shutil.rmtree(path, ignore_errors=True)

    def apply_patch(self, workspace_id: str, patch: bytes) -> None:
        """Apply an exact-file patch to the workspace."""
        path = self._workspace_path(workspace_id)
        subprocess.run(
            ["git", "apply", "-"],
            cwd=str(path),
            input=patch,
            capture_output=True,
            check=True,
        )

    def baseline_commit(self, workspace_id: str) -> str:
        """Return the baseline commit hash recorded at workspace creation."""
        path = self._workspace_path(workspace_id)
        baseline_file = path / "baseline.txt"
        if not baseline_file.exists():
            raise ValueError("Baseline not recorded")
        return baseline_file.read_text().strip()

    def candidate_commit(self, workspace_id: str) -> str:
        """Return the current HEAD commit hash."""
        path = self._workspace_path(workspace_id)
        return self._git(path, "rev-parse", "HEAD").strip()

    def commit(self, workspace_id: str, message: str) -> str:
        """Stage all changes and commit, returning the new hash."""
        path = self._workspace_path(workspace_id)
        self._git(path, "add", "-A")
        self._git(path, "commit", "-m", message)
        return self.candidate_commit(workspace_id)

    def escape_check(self, workspace_id: str) -> bool:
        """Return True if the workspace has escaped its isolation."""
        path = self._workspace_path(workspace_id)
        for root, dirs, files in os.walk(path):
            for name in dirs + files:
                full = Path(root) / name
                if full.is_symlink():
                    target = full.resolve()
                    # A symlink is an escape if it resolves outside the workspace directory.
                    if not target.is_relative_to(path):
                        return True
        return False

    def preserve(self, workspace_id: str) -> None:
        """Move a workspace to a preserved area so it is not destroyed."""
        path = self._workspace_path(workspace_id)
        preserved_dir = self.base_dir / "preserved"
        preserved_dir.mkdir(exist_ok=True)
        dest = preserved_dir / workspace_id
        if not dest.exists():
            shutil.move(str(path), str(dest))
        self._preserved.add(workspace_id)

    def is_preserved(self, workspace_id: str) -> bool:
        """Return True if the workspace has been preserved."""
        return workspace_id in self._preserved

    def _git(self, path: Path, *args: str) -> str:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(path),
            capture_output=True,
            text=True,
            check=True,
        )
        return proc.stdout
