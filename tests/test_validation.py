"""Tests for real command execution and honest validation evidence."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from doom_team.deliverable import DeliverableRepo
from doom_team.validation import (
    MAX_OUTPUT_CHARS,
    CommandResult,
    ValidationCommand,
    ValidationExecutionError,
    ValidationResult,
    execute_command,
    run_validation_commands,
)

PY = sys.executable


def test_structured_argv_executes_real_command(tmp_path: Path) -> None:
    result = execute_command(
        [PY, "-c", "print('hello-stdout')"], workspace_root=tmp_path
    )
    assert result.exit_code == 0
    assert "hello-stdout" in result.stdout
    assert result.timed_out is False
    assert result.argv[0] == PY
    assert result.cwd == str(tmp_path.resolve())
    assert result.duration_seconds >= 0


def test_stderr_and_nonzero_exit_captured(tmp_path: Path) -> None:
    result = execute_command(
        [PY, "-c", "import sys; sys.stderr.write('boom'); sys.exit(3)"],
        workspace_root=tmp_path,
    )
    assert result.exit_code == 3
    assert "boom" in result.stderr


def test_timeout_captured_as_failed_result(tmp_path: Path) -> None:
    result = execute_command(
        [PY, "-c", "import time; time.sleep(30)"],
        workspace_root=tmp_path,
        timeout=0.5,
    )
    assert result.timed_out is True
    validation = ValidationResult(label="hang", command=result)
    assert validation.passed is False


def test_cwd_escape_rejected(tmp_path: Path) -> None:
    inner = tmp_path / "workspace"
    inner.mkdir()
    with pytest.raises(ValidationExecutionError, match="escapes"):
        execute_command([PY, "-c", "pass"], workspace_root=inner, cwd=tmp_path)
    with pytest.raises(ValidationExecutionError, match="escapes"):
        execute_command([PY, "-c", "pass"], workspace_root=inner, cwd="../")


def test_shell_syntax_not_interpreted(tmp_path: Path) -> None:
    marker = tmp_path / "injected.txt"
    result = execute_command(
        ["echo", f"hello; touch {marker}", "&&", "true"], workspace_root=tmp_path
    )
    assert result.exit_code == 0
    assert not marker.exists()
    assert "&&" in result.stdout


def test_invalid_argv_rejected(tmp_path: Path) -> None:
    for bad in ([], [""], ["ok", "bad\x00arg"], [42]):
        with pytest.raises(ValidationExecutionError):
            execute_command(bad, workspace_root=tmp_path)  # type: ignore[arg-type]


def test_passed_is_derived_never_caller_supplied(tmp_path: Path) -> None:
    failing = execute_command([PY, "-c", "raise SystemExit(1)"], workspace_root=tmp_path)
    validation = ValidationResult(label="tests", command=failing)
    assert validation.passed is False
    # There is no field to override: passed is a read-only property derived
    # from the recorded execution.
    with pytest.raises(AttributeError):
        validation.passed = True  # type: ignore[misc]
    assert "passed" not in ValidationResult.__dataclass_fields__
    # Even a hand-built CommandResult cannot decouple passed from exit_code.
    fake = CommandResult(
        argv=("pytest",),
        cwd=str(tmp_path),
        exit_code=1,
        stdout="",
        stderr="",
        duration_seconds=0.0,
        timed_out=False,
    )
    assert ValidationResult(label="x", command=fake).passed is False


def test_output_bounded_for_evidence(tmp_path: Path) -> None:
    result = execute_command(
        [PY, "-c", f"print('x' * {MAX_OUTPUT_CHARS * 3})"], workspace_root=tmp_path
    )
    assert len(result.stdout) <= MAX_OUTPUT_CHARS
    assert result.stdout_truncated is True
    payload = ValidationResult(label="big", command=result).as_evidence_dict()
    assert len(json.dumps(payload)) < MAX_OUTPUT_CHARS * 2 + 4000
    assert payload["stdout_truncated"] is True


def test_evidence_dict_records_argv_cwd_exit(tmp_path: Path) -> None:
    result = execute_command([PY, "-c", "print('ok')"], workspace_root=tmp_path)
    payload = ValidationResult(label="smoke", command=result).as_evidence_dict()
    assert payload["argv"][0] == PY
    assert payload["cwd"] == str(tmp_path.resolve())
    assert payload["exit_code"] == 0
    assert payload["passed"] is True
    assert payload["timed_out"] is False


def test_empty_validation_plan_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValidationExecutionError, match="at least one"):
        run_validation_commands(tmp_path, [])


def test_disposable_end_to_end_integration(tmp_path: Path) -> None:
    # create -> isolated workspace -> fixture -> candidate -> REAL validation
    # -> candidate diff, all green.
    repo = DeliverableRepo.create(tmp_path / "project")
    workspace = repo.create_task_workspace("fixture-task", tmp_path / "ws")
    workspace.write_file(
        "calc.py", "def add(a, b):\n    return a + b\n"
    )
    workspace.write_file(
        "test_calc.py",
        "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n",
    )
    candidate = workspace.commit_candidate("add calculator and test")
    results = run_validation_commands(
        workspace.path,
        [
            ValidationCommand(label="compile", argv=(PY, "-m", "compileall", "-q", ".")),
            ValidationCommand(label="tests", argv=(PY, "-m", "pytest", "-q", "test_calc.py")),
        ],
    )
    assert all(result.passed for result in results)
    assert set(workspace.changed_files()) == {"calc.py", "test_calc.py"}
    assert "def add" in workspace.diff()
    assert repo.integrate_candidate(workspace) == candidate


def test_failing_real_command_yields_failed_evidence(tmp_path: Path) -> None:
    repo = DeliverableRepo.create(tmp_path / "project")
    workspace = repo.create_task_workspace("broken-task", tmp_path / "ws")
    workspace.write_file("test_broken.py", "def test_broken():\n    assert False\n")
    workspace.commit_candidate("add failing test")
    (result,) = run_validation_commands(
        workspace.path,
        [ValidationCommand(label="tests", argv=(PY, "-m", "pytest", "-q", "test_broken.py"))],
    )
    assert result.passed is False
    assert result.command.exit_code != 0


def test_binary_output_recorded_not_crashed(tmp_path: Path) -> None:
    result = execute_command(
        [PY, "-c", "import sys; sys.stdout.buffer.write(b'\\xff\\xfe\\x80 broken'); sys.exit(7)"],
        workspace_root=tmp_path,
    )
    assert result.exit_code == 7
    assert "broken" in result.stdout
    assert ValidationResult(label="bin", command=result).passed is False


def test_missing_binary_is_structured_error(tmp_path: Path) -> None:
    with pytest.raises(ValidationExecutionError, match="could not start"):
        execute_command(["definitely-not-a-real-binary-xyz"], workspace_root=tmp_path)


def test_torrential_output_spills_to_disk_and_stays_bounded(tmp_path: Path) -> None:
    # 40MB of output must neither crash nor balloon evidence.
    result = execute_command(
        [PY, "-c", "import sys\nfor _ in range(4000): sys.stdout.write('y' * 10000)"],
        workspace_root=tmp_path,
    )
    assert result.exit_code == 0
    assert len(result.stdout) <= MAX_OUTPUT_CHARS
    assert result.stdout_truncated is True
