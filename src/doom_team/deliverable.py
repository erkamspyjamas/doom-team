"""Deliverable project repository and isolated task workspaces.

The standalone mission needs one real destination repository that accumulates
accepted work, plus per-task isolated workspaces derived from an explicit
baseline. Workspaces are full local clones: changes there can never mutate
the deliverable until an explicit fast-forward-only integration. All Git
subprocesses run through the subprocess guard (argv-only, sanitized
environment). Nothing here pushes, configures remotes, merges external
branches, or deploys - integration is local and fail-closed.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from doom_team.subprocess_guard import run_guarded

_TASK_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,80}$")
_BASELINE_SHA = re.compile(r"^[0-9a-f]{7,40}$")
_REVISION_SHA = _BASELINE_SHA
_GIT_IDENTITY = ("doom-team", "team@doom.local")

# `.git/info/attributes` outranks every in-tree `.gitattributes`, and lives
# where workspace writes are refused. Pinning it means committed content
# cannot hide itself from review (`-diff` rendering as "Binary files differ"),
# nor make the checked-out bytes differ from the committed blob through eol
# conversion, clean/smudge filters, or keyword expansion.
_WORKSPACE_ATTRIBUTES = (
    "* diff -text -filter -ident -export-subst -export-ignore "
    "-working-tree-encoding\n"
)


def _write_workspace_attributes(repo: Path) -> None:
    info_dir = repo / ".git" / "info"
    info_dir.mkdir(parents=True, exist_ok=True)
    (info_dir / "attributes").write_text(_WORKSPACE_ATTRIBUTES, encoding="utf-8")


class DeliverableError(Exception):
    """Raised when a deliverable repository operation is unsafe or invalid."""


# Git reads several config keys as COMMANDS to execute. Validation runs
# model-authored code beside the workspace with no filesystem sandbox, so
# `.git/config` there must be assumed hostile: a planted `core.fsmonitor`
# would run on the next `git status`, inside this process. Passing these on
# the command line overrides whatever the file says, on every invocation, so
# rewriting the file later buys nothing.
_GIT_HARDENING: tuple[str, ...] = (
    "-c", "core.fsmonitor=false",
    "-c", "core.hooksPath=/dev/null",
    "-c", "core.pager=cat",
    "-c", "protocol.ext.allow=never",
)
# An empty `diff.external` would itself be run as a command, so the external
# differ is disabled with the flag git provides for it instead.
_NO_EXTERNAL_DIFF = ("--no-ext-diff",)


def _git(path: Path, *args: str) -> str:
    completed = run_guarded(
        ["git", *_GIT_HARDENING, *args],
        cwd=str(path),
        check=False,
        text=True,
        capture_output=True,
    )
    if completed.returncode != 0:
        raise DeliverableError(
            f"git {args[0]} failed in {path}: {completed.stderr.strip()[:500]}"
        )
    return str(completed.stdout)


def _source_tree_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _safe_destination(destination: str | Path) -> Path:
    raw = str(destination)
    if not raw.strip() or "\x00" in raw:
        raise DeliverableError("destination path must be a non-empty safe path")
    dest = Path(destination).expanduser().resolve()
    if dest == Path("/") or dest == Path.home():
        raise DeliverableError("destination path is too broad to be a project repo")
    source_root = _source_tree_root()
    if dest.is_relative_to(source_root):
        raise DeliverableError(
            "destination must be outside the Doom Team source tree"
        )
    return dest


@dataclass(frozen=True)
class TaskWorkspace:
    """An isolated clone-based workspace for one task."""

    task_id: str
    path: Path
    baseline_sha: str
    branch: str

    def write_file(self, relative: str, content: str) -> Path:
        """Write one file at a safe repository-relative path."""
        if not relative or "\x00" in relative:
            raise DeliverableError("workspace file path must be non-empty and NUL-free")
        parts = Path(relative)
        # Path(".") and Path("./") normalize to an EMPTY parts tuple, which
        # would make every part-wise guard below vacuously true; reject such
        # non-file paths explicitly rather than indexing into nothing.
        if not parts.parts:
            raise DeliverableError(f"workspace file path names no file: {relative}")
        if parts.is_absolute() or any(part in ("", ".", "..") for part in parts.parts):
            raise DeliverableError(f"unsafe workspace file path: {relative}")
        if parts.parts[0] == ".git":
            raise DeliverableError("workspace files cannot target Git metadata")
        target = (self.path / parts).resolve()
        if not target.is_relative_to(self.path.resolve()):
            raise DeliverableError(f"workspace file path escapes the workspace: {relative}")
        current = self.path.resolve()
        for part in parts.parts[:-1]:
            current = current / part
            if current.is_symlink():
                raise DeliverableError(f"workspace file path crosses a symlink: {relative}")
        # The final component too: an existing symlink AT the target would
        # redirect the write (e.g. into .git) even when its destination
        # resolves inside the workspace.
        if (self.path / parts).is_symlink():
            raise DeliverableError(f"workspace file path is a symlink: {relative}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target

    def is_clean(self) -> bool:
        return not _git(self.path, "status", "--porcelain").strip()

    def commit_candidate(self, message: str) -> str:
        """Stage everything and commit; returns the candidate SHA."""
        if not message.strip():
            raise DeliverableError("candidate commit message must not be empty")
        _git(self.path, "add", "-A")
        if not _git(self.path, "status", "--porcelain").strip():
            raise DeliverableError("nothing to commit in the task workspace")
        _git(self.path, "commit", "-q", "-m", message)
        return self.candidate_sha()

    def candidate_sha(self) -> str:
        return _git(self.path, "rev-parse", "HEAD").strip()

    def changed_files(self, revision: str = "HEAD") -> tuple[str, ...]:
        """Files changed between the baseline and a candidate revision.

        NUL-separated so git never C-quotes non-ASCII or quote-bearing paths
        into an unusable rendering.
        """
        output = _git(
            self.path, "diff", *_NO_EXTERNAL_DIFF, "--name-only", "-z",
            f"{self.baseline_sha}..{revision}",
        )
        return tuple(name for name in output.split("\0") if name)

    def files_in_revision(self, revision: str = "HEAD") -> tuple[str, ...]:
        """Every path present in the given revision's tree (NUL-separated)."""
        output = _git(self.path, "ls-tree", "-r", "--name-only", "-z", revision)
        return tuple(name for name in output.split("\0") if name)

    def diff(self, revision: str = "HEAD") -> str:
        """Complete textual diff between baseline and a candidate revision.

        The workspace pins ``.git/info/attributes`` (see
        :meth:`DeliverableRepo.create_task_workspace`), which outranks any
        in-tree ``.gitattributes``, so committed content can never suppress
        or rewrite its own rendering here.
        """
        return _git(
            self.path, "diff", *_NO_EXTERNAL_DIFF, f"{self.baseline_sha}..{revision}"
        )

    def export_revision(self, destination: str | Path, revision: str) -> Path:
        """Materialize a revision into a fresh, disposable repository copy.

        Validation runs against this export rather than the live workspace:
        code executed during validation therefore cannot alter - or even
        reach - the reviewed candidate or its object store.
        """
        if not _REVISION_SHA.fullmatch(revision.strip()):
            raise DeliverableError(f"export revision must be a commit SHA: {revision!r}")
        dest = Path(destination).expanduser().resolve()
        if dest.exists():
            raise DeliverableError(f"export destination already exists: {dest}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        # --no-checkout first: the pin must be in place BEFORE any file is
        # materialized, or the clone's own checkout would apply the in-tree
        # attributes it is meant to override.
        _git(
            dest.parent,
            "clone",
            "--no-checkout",
            "--no-hardlinks",
            "-q",
            str(self.path),
            str(dest),
        )
        _write_workspace_attributes(dest)
        _git(dest, "checkout", "-q", "--detach", revision.strip())
        _git(dest, "remote", "remove", "origin")
        return dest


@dataclass(frozen=True)
class DeliverableRepo:
    """The destination repository accumulating accepted mission work."""

    path: Path

    @classmethod
    def create(cls, destination: str | Path) -> "DeliverableRepo":
        """Initialize a brand-new deliverable repository fail-closed."""
        dest = _safe_destination(destination)
        if dest.exists():
            if not dest.is_dir():
                raise DeliverableError(f"destination {dest} exists and is not a directory")
            if any(dest.iterdir()):
                raise DeliverableError(f"destination {dest} is not empty")
        else:
            if not dest.parent.exists():
                raise DeliverableError(f"destination parent {dest.parent} does not exist")
            dest.mkdir()
        _git(dest, "init", "-q", "-b", "main")
        # The same pin workspaces get. The deliverable is what a human
        # inspects and approves, so committed content must not be able to
        # hide its own diff or rewrite how it renders.
        _write_workspace_attributes(dest)
        _git(dest, "config", "user.name", _GIT_IDENTITY[0])
        _git(dest, "config", "user.email", _GIT_IDENTITY[1])
        _git(dest, "commit", "-q", "--allow-empty", "-m", "doom-team: initial baseline")
        return cls(path=dest)

    @classmethod
    def open(cls, destination: str | Path) -> "DeliverableRepo":
        dest = _safe_destination(destination)
        if not (dest / ".git").is_dir():
            raise DeliverableError(f"{dest} is not a deliverable repository")
        # Re-assert the pin: a repository created before this guard existed,
        # or one whose metadata was tampered with, is hardened on open.
        _write_workspace_attributes(dest)
        return cls(path=dest)

    def head(self) -> str:
        return _git(self.path, "rev-parse", "HEAD").strip()

    def is_clean(self) -> bool:
        return not _git(self.path, "status", "--porcelain").strip()

    def contains(self, revision: str) -> bool:
        """True when ``revision`` is already an ancestor of (or equal to) HEAD.

        Git truth for crash reconciliation: it answers "was this candidate
        already integrated?" without trusting any external bookkeeping.
        """
        candidate = revision.strip()
        if not _BASELINE_SHA.fullmatch(candidate):
            raise DeliverableError(f"revision must be a commit SHA: {revision!r}")
        # A candidate that was never fetched is simply not here yet; that is an
        # answer, not an error. Ask before comparing, so "unknown object" and
        # "genuinely broken repository" stay distinguishable.
        present = run_guarded(
            ["git", *_GIT_HARDENING, "cat-file", "-e", f"{candidate}^{{commit}}"],
            cwd=str(self.path),
            check=False,
            text=True,
            capture_output=True,
        )
        if present.returncode != 0:
            return False
        completed = run_guarded(
            ["git", *_GIT_HARDENING, "merge-base", "--is-ancestor", candidate, "HEAD"],
            cwd=str(self.path),
            check=False,
            text=True,
            capture_output=True,
        )
        if completed.returncode == 0:
            return True
        if completed.returncode == 1:
            return False
        raise DeliverableError(
            f"git merge-base failed in {self.path}: {completed.stderr.strip()[:200]}"
        )

    def remotes(self) -> tuple[str, ...]:
        output = _git(self.path, "remote")
        return tuple(line for line in output.splitlines() if line.strip())

    def create_task_workspace(
        self,
        task_id: str,
        workspaces_root: str | Path,
        *,
        baseline_sha: str | None = None,
    ) -> TaskWorkspace:
        """Clone an isolated task workspace at an explicit baseline."""
        if not _TASK_ID.fullmatch(task_id):
            raise DeliverableError(f"unsafe task id for workspace: {task_id!r}")
        root = Path(workspaces_root).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        if root.is_relative_to(self.path.resolve()):
            raise DeliverableError("workspaces root cannot live inside the deliverable repo")
        baseline = (baseline_sha or self.head()).strip()
        # Strict hex only: a dash-prefixed or symbolic value handed to git
        # would be parsed as an option (option injection) or resolve to the
        # wrong baseline; verify it names a real commit before any checkout.
        if not _BASELINE_SHA.fullmatch(baseline):
            raise DeliverableError(f"baseline must be a commit SHA: {baseline!r}")
        object_type = _git(self.path, "cat-file", "-t", baseline).strip()
        if object_type != "commit":
            raise DeliverableError(f"baseline {baseline} is not a commit")
        workspace_path = root / f"{task_id}-{uuid.uuid4().hex[:8]}"
        _git(
            root,
            "clone",
            "--no-checkout",
            "--no-hardlinks",
            "-q",
            str(self.path),
            str(workspace_path),
        )
        _write_workspace_attributes(workspace_path)
        _git(workspace_path, "config", "user.name", _GIT_IDENTITY[0])
        _git(workspace_path, "config", "user.email", _GIT_IDENTITY[1])
        _git(workspace_path, "checkout", "-q", baseline)
        branch = f"task/{task_id}"
        _git(workspace_path, "checkout", "-q", "-b", branch)
        # The clone keeps an origin pointing at the deliverable; remove it so
        # no workspace operation can ever push back implicitly.
        _git(workspace_path, "remote", "remove", "origin")
        return TaskWorkspace(
            task_id=task_id,
            path=workspace_path,
            baseline_sha=baseline,
            branch=branch,
        )

    def integrate_candidate(self, workspace: TaskWorkspace) -> str:
        """Fast-forward-only local integration of a committed candidate.

        Fail closed: a dirty deliverable, a dirty workspace, or a non-ff
        candidate is rejected. This is mechanical advancement - acceptance
        semantics (QA/Reviewer/approval) live above this layer.
        """
        if not self.is_clean():
            raise DeliverableError("deliverable repository is dirty; refusing to integrate")
        # Untracked artifacts (test caches, __pycache__) cannot change the
        # fetched candidate commit; only uncommitted TRACKED changes would be
        # silently lost, so exactly those block integration.
        tracked_dirt = _git(
            workspace.path, "status", "--porcelain", "--untracked-files=no"
        ).strip()
        if tracked_dirt:
            raise DeliverableError(
                "task workspace has uncommitted tracked changes; "
                "commit the candidate first"
            )
        _git(self.path, "fetch", "-q", str(workspace.path), workspace.branch)
        try:
            _git(self.path, "merge", "--ff-only", "-q", "FETCH_HEAD")
        except DeliverableError as exc:
            raise DeliverableError(
                f"candidate for task {workspace.task_id} is not a fast-forward "
                f"of the deliverable head: {exc}"
            ) from exc
        return self.head()
