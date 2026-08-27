"""Real deterministic validation: executed commands -> honest evidence.

:func:`execute_command` produces the immutable :class:`CommandResult` from an
actual subprocess execution, and :class:`ValidationResult` DERIVES pass/fail
from the recorded exit status and timeout state - ``passed`` is a read-only
property, so it can never silently diverge from the recorded execution via
normal field assignment. (Python cannot prevent deliberate in-process
fabrication - subclass overrides or hand-built records - so downstream
acceptance layers must never trust result instances handed across a trust
boundary.) Output is captured to bounded spill files, decoded tolerantly, and
truncated before it can reach durable evidence, so binary or torrential
output becomes honest evidence rather than a crash. Commands run argv-only
(never through a shell) with the subprocess guard's sanitized environment and
best-effort network isolation, confined to the candidate workspace.
"""

from __future__ import annotations

import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from doom_team.subprocess_guard import network_isolation_prefix, sanitize_env

MAX_OUTPUT_CHARS = 24_000
DEFAULT_COMMAND_TIMEOUT = 300.0
TIMEOUT_EXIT_CODE = -1


class ValidationExecutionError(Exception):
    """Raised when a validation command cannot be executed safely."""


@dataclass(frozen=True)
class CommandResult:
    """Immutable record of one actually-executed command."""

    argv: tuple[str, ...]
    cwd: str
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool
    stdout_truncated: bool = False
    stderr_truncated: bool = False


@dataclass(frozen=True)
class ValidationCommand:
    """One structured validation command in a task's validation plan."""

    label: str
    argv: tuple[str, ...]


@dataclass(frozen=True)
class ValidationResult:
    """Pass/fail evidence derived from a real :class:`CommandResult`.

    ``passed`` is intentionally a derived property: there is no field a
    caller could set to claim success contrary to the recorded execution.
    """

    label: str
    command: CommandResult

    @property
    def passed(self) -> bool:
        return self.command.exit_code == 0 and not self.command.timed_out

    def as_evidence_dict(self) -> dict[str, Any]:
        """Bounded, serializable, secret-free evidence representation."""
        return {
            "label": self.label,
            "argv": list(self.command.argv),
            "cwd": self.command.cwd,
            "exit_code": self.command.exit_code,
            "timed_out": self.command.timed_out,
            "passed": self.passed,
            "duration_seconds": round(self.command.duration_seconds, 3),
            "stdout": self.command.stdout,
            "stderr": self.command.stderr,
            "stdout_truncated": self.command.stdout_truncated,
            "stderr_truncated": self.command.stderr_truncated,
        }


# How much of the END of an oversized capture survives. Tooling prints its
# verdict last - pytest's failure summary and the raised exception are the
# final lines - so a head-only bound here would amputate exactly the content
# every later remediation needs, no matter how carefully the ledger clips.
MAX_OUTPUT_TAIL_CHARS = 4_000
_CAPTURE_CLIP_MARKER = "\n[... output clipped ...]\n"


def _bounded(text: str) -> tuple[str, bool]:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text, False
    head = MAX_OUTPUT_CHARS - MAX_OUTPUT_TAIL_CHARS - len(_CAPTURE_CLIP_MARKER)
    if head <= 0:
        return text[-MAX_OUTPUT_CHARS:], True
    return (
        text[:head] + _CAPTURE_CLIP_MARKER + text[-MAX_OUTPUT_TAIL_CHARS:],
        True,
    )


def _bounded_from_file(handle: Any) -> tuple[str, bool]:
    """Read a bounded, tolerantly-decoded head AND tail of a spill file."""
    handle.flush()
    size = handle.seek(0, 2)
    head_bytes = MAX_OUTPUT_CHARS * 4
    handle.seek(0)
    raw = handle.read(head_bytes)
    if size > len(raw):
        # The file outgrew the head window: fetch its real end as well, so
        # the terminal error lines survive into the bounded text below.
        tail_bytes = MAX_OUTPUT_TAIL_CHARS * 4
        handle.seek(max(0, size - tail_bytes))
        tail_raw = handle.read(tail_bytes)
        decoded = (
            raw.decode("utf-8", errors="replace")
            + _CAPTURE_CLIP_MARKER
            + tail_raw.decode("utf-8", errors="replace")
        )
    else:
        decoded = raw.decode("utf-8", errors="replace")
    text, char_truncated = _bounded(decoded)
    return text, char_truncated or size > len(raw)


def _validate_argv(argv: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    if not argv:
        raise ValidationExecutionError("validation argv must not be empty")
    for item in argv:
        if not isinstance(item, str) or not item:
            raise ValidationExecutionError("validation argv entries must be non-empty strings")
        if "\x00" in item:
            raise ValidationExecutionError("validation argv entries must not contain NUL")
    return tuple(argv)


def _confined_cwd(cwd: str | Path, workspace_root: str | Path) -> Path:
    root = Path(workspace_root).resolve()
    target = Path(cwd)
    if not target.is_absolute():
        target = root / target
    resolved = target.resolve()
    if not resolved.is_relative_to(root):
        raise ValidationExecutionError(
            f"validation cwd {cwd} escapes the workspace root"
        )
    if not resolved.is_dir():
        raise ValidationExecutionError(f"validation cwd {cwd} is not a directory")
    current = root
    for part in resolved.relative_to(root).parts:
        current = current / part
        if current.is_symlink():
            raise ValidationExecutionError(
                f"validation cwd {cwd} contains a symlink component"
            )
    return resolved


def execute_command(
    argv: tuple[str, ...] | list[str],
    *,
    workspace_root: str | Path,
    cwd: str | Path | None = None,
    timeout: float = DEFAULT_COMMAND_TIMEOUT,
) -> CommandResult:
    """Execute one structured command inside the workspace and record it.

    Never uses a shell; the sanitized-environment and network-isolation
    guards of :func:`run_guarded` apply. A timeout is recorded as an explicit
    failed result rather than an exception, so evidence always reflects what
    actually happened.
    """
    safe_argv = _validate_argv(argv)
    resolved_cwd = _confined_cwd(cwd if cwd is not None else workspace_root, workspace_root)
    command = network_isolation_prefix() + list(safe_argv)
    environment = sanitize_env()
    started = time.monotonic()
    # Child output spills to disk files (never pipes held in memory) so a
    # torrential command cannot exhaust validator memory; only a bounded
    # prefix is decoded (errors='replace') into evidence.
    with tempfile.TemporaryFile() as out_file, tempfile.TemporaryFile() as err_file:
        timed_out = False
        try:
            completed_code: int = subprocess.run(
                command,
                cwd=str(resolved_cwd),
                env=environment,
                stdout=out_file,
                stderr=err_file,
                check=False,
                timeout=timeout,
            ).returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            completed_code = TIMEOUT_EXIT_CODE
        except OSError as exc:
            raise ValidationExecutionError(
                f"validation command could not start: {exc}"
            ) from exc
        stdout, stdout_truncated = _bounded_from_file(out_file)
        stderr, stderr_truncated = _bounded_from_file(err_file)
        return CommandResult(
            argv=safe_argv,
            cwd=str(resolved_cwd),
            exit_code=int(completed_code),
            stdout=stdout,
            stderr=stderr,
            duration_seconds=time.monotonic() - started,
            timed_out=timed_out,
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
        )


def run_validation_commands(
    workspace_root: str | Path,
    commands: tuple[ValidationCommand, ...] | list[ValidationCommand],
    *,
    timeout: float = DEFAULT_COMMAND_TIMEOUT,
) -> tuple[ValidationResult, ...]:
    """Execute every validation command for real and derive honest results."""
    if not commands:
        raise ValidationExecutionError("a validation plan must contain at least one command")
    results: list[ValidationResult] = []
    for command in commands:
        result = execute_command(
            command.argv, workspace_root=workspace_root, timeout=timeout
        )
        results.append(ValidationResult(label=command.label, command=result))
    return tuple(results)


