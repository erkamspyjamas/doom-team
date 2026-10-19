"""Repository-sized context and complete-file replacement regressions."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from doom_team.deliverable import DeliverableRepo, TaskWorkspace
from doom_team.domain import TaskContract
from doom_team.routing import route_task, specialist_by_id
from doom_team.task_runner import ProductionTaskRunner
from doom_team.validation import ValidationCommand
from doom_team.workspace_context import MAX_CONTEXT_TOTAL_CHARS, build_workspace_context


def _workspace(tmp_path: Path, files: dict[str, str]) -> TaskWorkspace:
    repo = DeliverableRepo.create(tmp_path / "project")
    seed = repo.create_task_workspace("seed", tmp_path / "workspaces")
    for name, text in files.items():
        seed.write_file(name, text)
    seed.commit_candidate("seed repository")
    repo.integrate_candidate(seed)
    return repo.create_task_workspace("wire-usage-cli", tmp_path / "workspaces")


def _task() -> TaskContract:
    return TaskContract(
        id="wire-usage-cli",
        objective_id="usage-report",
        instruction=(
            "Update doom_team/cli.py, which registers backup/restore, to register usage "
            "using the aggregation module. Implement state dir precedence, offline operation and tests."
        ),
        acceptance_criteria=(
            "Preserve the existing parser and main dispatch",
            "State dir precedence, provider routing, packaging, backup restore, approval and validation tests pass",
        ),
        dependencies=("implement-usage-aggregation",),
        required_capabilities=("backend.python",),
    )


@dataclass
class _Response:
    content: str


class _Inference:
    def __init__(self, files: dict[str, str]) -> None:
        self.calls: list[list[dict[str, str]]] = []
        self.files = files

    def complete(self, messages: list[dict[str, str]]) -> _Response:
        self.calls.append(messages)
        if len(self.calls) == 1:
            return _Response(json.dumps({
                "files": [{"path": name, "content": text} for name, text in self.files.items()],
                "summary": "integrate usage command",
            }))
        return _Response('{"verdict":"pass","summary":"checked","blockers":[]}')


def _run(workspace: TaskWorkspace, inference: _Inference, **kwargs: str):
    task = _task()
    decision = route_task(task)
    commands = (ValidationCommand(label="compile", argv=(sys.executable, "-m", "compileall", "-q", ".")),)
    return ProductionTaskRunner(inference, validation_commands=commands).run_task(
        task, decision, specialist_by_id(decision.specialist_id), workspace, **kwargs
    )


def test_large_repository_sends_complete_cli_and_accepted_dependency(tmp_path: Path) -> None:
    cli = "def parser(): pass\n" + "# existing parser code\n" * 1050 + "def main(): return 'PRESERVE_DISPATCH'\n"
    usage = "# aggregation implementation\n" * 400 + "def aggregate_usage(): return 'CANONICAL_API'\n"
    files = {f"docs/architecture-{index}.md": "unrelated documentation\n" * 1000 for index in range(8)}
    files.update({
        "src/doom_team/backup.py": "# existing backup implementation\n" * 120,
        "tests/test_state_dir_precedence.py": "# state tests\n" * 700,
        "tests/test_backup_restore.py": "# backup tests\n" * 450,
        "tests/test_approval.py": "# approval tests\n" * 300,
        "tests/test_mission_recovery.py": "# recovery tests\n" * 1500,
    })
    files.update({"src/doom_team/cli.py": cli, "src/doom_team/usage.py": usage})
    workspace = _workspace(tmp_path, files)
    inference = _Inference({"src/doom_team/cli.py": cli + "\n# register usage\n"})
    result = _run(
        workspace, inference,
        dependency_summary="already integrated tasks: implement-usage-aggregation",
        remediation_summary=(
            "FAILED tests/test_mission_recovery.py: timestamp assertion failed; preserve backup behavior"
        ),
    )
    assert result.status == "accepted"
    prompt = inference.calls[0][1]["content"]
    assert cli in prompt and usage in prompt
    assert "PRESERVE_DISPATCH" in prompt and "CANONICAL_API" in prompt
    assert len(inference.calls) == 3  # Independent gates still execute.
    context = build_workspace_context(workspace, query=_task().instruction)
    assert len(context.text) <= MAX_CONTEXT_TOTAL_CHARS
    assert "omitted: total context budget" in context.text


def test_direct_reference_wins_over_earlier_alphabetical_files(tmp_path: Path) -> None:
    target = "TARGET_START\n" + "x" * 23_000 + "\nTARGET_END"
    workspace = _workspace(tmp_path, {"a.txt": "a" * 30_000, "z/target.txt": target})
    context = build_workspace_context(workspace, query="Update z/target.txt")
    assert target in context.text
    assert "z/target.txt" in context.complete_paths
    assert "a.txt" not in context.complete_paths
    assert context == build_workspace_context(workspace, query="Update z/target.txt")


def test_dependency_and_feedback_references_participate_in_real_prompt(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, {
        "a.txt": "a" * 30_000,
        "z/api.txt": "DEPENDENCY_API\n" + "z" * 30_000,
    })
    inference = _Inference({"new.txt": "new content"})
    result = _run(workspace, inference, dependency_summary="z/api.txt implements the accepted API")
    assert result.status == "accepted"
    prompt = inference.calls[0][1]["content"]
    assert "DEPENDENCY_API\n" + "z" * 30_000 in prompt
    # Retry feedback also promotes a previously omitted file deterministically.
    context = build_workspace_context(workspace, query="existing file z/api.txt was omitted")
    assert "z/api.txt" in context.complete_paths


def test_total_budget_omission_blocks_all_writes_before_candidate_commit(tmp_path: Path) -> None:
    original = "z\n" * 15_000
    workspace = _workspace(tmp_path, {"a.txt": "a" * 30_000, "z.txt": original})
    context = build_workspace_context(workspace)
    assert "z.txt" not in context.complete_paths
    inference = _Inference({"new.txt": "must not be written", "./z.txt": "guessed replacement"})
    result = _run(workspace, inference)
    assert result.status == "execution_failed"
    assert "z.txt was omitted" in result.failure_detail
    assert result.candidate_sha is None
    assert workspace.candidate_sha() == workspace.baseline_sha
    assert (workspace.path / "z.txt").read_text() == original
    assert not (workspace.path / "new.txt").exists()
    assert len(inference.calls) == 1
    retry = _Inference({"z.txt": original + "updated\n"})
    recovered = _run(workspace, retry, remediation_summary=result.failure_detail)
    assert recovered.status == "accepted"
    assert original in retry.calls[0][1]["content"]


@pytest.mark.parametrize("character", ["x", "\U0001f680"], ids=("ascii", "four-byte-utf8"))
def test_file_at_character_limit_is_included_whole(tmp_path: Path, character: str) -> None:
    text = character * 50_000
    workspace = _workspace(tmp_path, {"boundary.txt": text})
    context = build_workspace_context(workspace, query="boundary.txt")
    assert text in context.text
    assert "boundary.txt" in context.complete_paths
    assert len(context.text) <= MAX_CONTEXT_TOTAL_CHARS


@pytest.mark.parametrize("text", ["x" * 50_001, "é" * 50_001], ids=("ascii", "unicode"))
def test_oversized_file_is_explicitly_omitted_without_a_prefix(tmp_path: Path, text: str) -> None:
    workspace = _workspace(tmp_path, {"huge.txt": text, "small.txt": "SMALL_COMPLETE"})
    context = build_workspace_context(workspace, query="Rewrite huge.txt")
    assert '"huge.txt" [omitted: per-file limit]' in context.text
    assert "huge.txt" not in context.complete_paths
    assert text[:100] not in context.text
    assert "SMALL_COMPLETE" in context.text


def test_oversized_existing_file_cannot_be_replaced_from_guesses(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, {"huge.txt": "x" * 50_001})
    inference = _Inference({"huge.txt": "short guessed replacement"})
    result = _run(workspace, inference)
    assert result.status == "execution_failed" and "complete source" in result.failure_detail
    assert (workspace.path / "huge.txt").stat().st_size == 50_001


def test_binary_invalid_text_and_symlinks_never_expose_partial_or_external_data(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, {"binary.bin": "seed", "invalid.txt": "seed", "link.txt": "seed"})
    (workspace.path / "binary.bin").write_bytes(b"BINARY_PRIVATE\x00")
    (workspace.path / "invalid.txt").write_bytes(b"INVALID_PRIVATE\xff")
    external = tmp_path / "external.txt"
    external.write_text("EXTERNAL_PRIVATE")
    (workspace.path / "link.txt").unlink()
    (workspace.path / "link.txt").symlink_to(external)
    context = build_workspace_context(workspace)
    assert "PRIVATE" not in context.text
    assert not context.complete_paths
    assert "omitted: binary" in context.text
    assert "omitted: invalid UTF-8" in context.text
    assert "omitted: symlink" in context.text


def test_non_ascii_space_and_newline_paths_are_read_without_git_quoting(tmp_path: Path) -> None:
    files = {"café/计算.py": "UNICODE_BODY", "with space.txt": "SPACE_BODY", "line\nbreak.txt": "NEWLINE_BODY"}
    workspace = _workspace(tmp_path, files)
    context = build_workspace_context(workspace)
    assert context.tracked_paths == context.complete_paths == frozenset(files)
    assert all(body in context.text for body in files.values())
    assert '"line\\nbreak.txt" [complete]' in context.text


def test_inventory_and_framing_cannot_exceed_the_total_bound(tmp_path: Path) -> None:
    files = {f"{index:04}-{'long-name-' * 8}.txt": "body\n" for index in range(400)}
    workspace = _workspace(tmp_path, files)
    context = build_workspace_context(workspace)
    assert len(context.text) <= MAX_CONTEXT_TOTAL_CHARS
    assert "inventory truncated:" in context.text
    assert "tracked files: 400" in context.text
    assert context.tracked_paths == frozenset(files)
