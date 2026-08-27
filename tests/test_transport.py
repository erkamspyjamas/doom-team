"""Transport seam: identity binding, result authority, and fail-closed behaviour.

These exercise the transport contract itself and need no Gas City daemon: the
Gate-A in-process path must keep working with nothing external running.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from doom_team.domain import TaskContract
from doom_team.gas_city_transport import GasCityTaskTransport
from doom_team.routing import route_task
from doom_team.task_runner import RoleVerdict, TaskExecutionResult
from doom_team.transport import (
    InProcessTaskTransport,
    TaskAttemptRequest,
    TransportError,
    resolve_validation_policy,
    result_from_dict,
    result_to_dict,
    transport_failure,
    verify_result_identity,
)
from doom_team.validation import CommandResult, ValidationResult

MISSION = "mission-aaaaaaaaaaaa-11112222"


def _task(task_id: str = "arith-code") -> TaskContract:
    return TaskContract(
        id=task_id,
        objective_id="obj1",
        instruction="Implement the arithmetic module",
        acceptance_criteria=("add works",),
        required_capabilities=("backend.python",),
    )


def _request(**overrides: Any) -> TaskAttemptRequest:
    task = overrides.pop("task", _task())
    fields: dict[str, Any] = dict(
        mission_id=MISSION,
        task_id=task.id,
        attempt=1,
        baseline_sha="a" * 40,
        workspace_dir="ws-1",
        branch="task/arith-code",
        workspaces_root="/tmp/work",
        task=task,
        decision=route_task(task),
    )
    fields.update(overrides)
    return TaskAttemptRequest(**fields)


def _result(**overrides: Any) -> TaskExecutionResult:
    fields: dict[str, Any] = dict(
        task_id="arith-code",
        specialist_id="python_backend",
        baseline_sha="a" * 40,
        status="accepted",
        candidate_sha="b" * 40,
        validation_results=(
            ValidationResult(
                label="tests",
                command=CommandResult(
                    argv=("python", "-m", "pytest"), cwd="/tmp/ws", exit_code=0,
                    stdout="1 passed", stderr="", duration_seconds=0.1, timed_out=False,
                ),
            ),
        ),
        qa=RoleVerdict(role="qa", verdict="pass", summary="ok"),
        reviewer=RoleVerdict(role="reviewer", verdict="pass", summary="ok"),
    )
    fields.update(overrides)
    return TaskExecutionResult(**fields)


# --- envelope integrity ----------------------------------------------------


def test_request_round_trips_without_losing_identity() -> None:
    request = _request()
    restored = TaskAttemptRequest.from_dict(json.loads(json.dumps(request.to_dict())))
    assert restored.identity == request.identity
    assert restored.baseline_sha == request.baseline_sha
    assert restored.task == request.task
    assert restored.decision == request.decision


def test_request_rejects_inconsistent_identity() -> None:
    with pytest.raises(TransportError, match="task contract"):
        _request(task_id="other")
    with pytest.raises(TransportError, match="start at 1"):
        _request(attempt=0)
    with pytest.raises(TransportError, match="pinned to a baseline"):
        _request(baseline_sha="")


def test_request_envelope_carries_no_executable_commands() -> None:
    """A request names a validation policy; it can never supply argv."""
    payload = _request().to_dict()
    flat = json.dumps(payload)
    assert "argv" not in flat
    assert payload["validation_policy"] == "default"
    assert resolve_validation_policy("default")
    with pytest.raises(TransportError, match="unknown validation policy"):
        resolve_validation_policy("; rm -rf /")


def test_malformed_request_fails_closed() -> None:
    good = _request().to_dict()
    for mutate in (
        lambda d: d.update(schema_version=99),
        lambda d: d.pop("task"),
        lambda d: d.pop("decision"),
        lambda d: d.update(attempt="not-a-number"),
        lambda d: d["task"].pop("acceptance_criteria"),
    ):
        payload = json.loads(json.dumps(good))
        mutate(payload)
        with pytest.raises(TransportError):
            TaskAttemptRequest.from_dict(payload)


# --- result authority ------------------------------------------------------


def test_result_round_trips_and_derives_passed_from_exit_code() -> None:
    restored = result_from_dict(json.loads(json.dumps(result_to_dict(_result()))))
    assert restored.status == "accepted"
    assert restored.validation_results[0].passed is True
    assert restored.qa is not None and restored.qa.passed


def test_a_forged_passed_flag_cannot_claim_a_green_run() -> None:
    """`passed` is recomputed from the exit code, never read from the payload."""
    payload = result_to_dict(_result())
    payload["validation_results"][0]["command"]["exit_code"] = 1
    payload["validation_results"][0]["passed"] = True  # a lie
    restored = result_from_dict(payload)
    assert restored.validation_results[0].passed is False


def test_malformed_result_fails_closed() -> None:
    good = result_to_dict(_result())
    for mutate in (
        lambda d: d.pop("task_id"),
        lambda d: d.update(qa={"verdict": "definitely-fine"}),
        lambda d: d["validation_results"][0].pop("command"),
        lambda d: d["validation_results"][0]["command"].update(exit_code="zero"),
    ):
        payload = json.loads(json.dumps(good))
        mutate(payload)
        with pytest.raises(TransportError):
            result_from_dict(payload)


@pytest.mark.parametrize(
    "overrides,match",
    [
        ({"task_id": "other-task"}, "belongs to task"),
        ({"baseline_sha": "f" * 40}, "baseline"),
        ({"specialist_id": "technical_writer"}, "specialist"),
    ],
)
def test_result_identity_is_verified(overrides: dict[str, Any], match: str) -> None:
    with pytest.raises(TransportError, match=match):
        verify_result_identity(_request(), _result(**overrides))


def test_transport_failure_is_a_failed_attempt_not_a_success() -> None:
    result = transport_failure(_request(), "gas city unavailable")
    assert result.status == "execution_failed"
    assert result.candidate_sha is None
    assert result.qa is None and result.reviewer is None
    assert "gas city unavailable" in result.failure_detail


# --- Gas City transport failure paths (no daemon needed) -------------------


def _transport(tmp_path: Path, **kwargs: Any) -> GasCityTaskTransport:
    fields: dict[str, Any] = dict(
        city=tmp_path / "city",
        gc_binary=tmp_path / "missing-gc",
        jobs_root=tmp_path / "jobs",
    )
    fields.update(kwargs)
    return GasCityTaskTransport(**fields)


def test_gas_city_unavailable_is_a_typed_failed_attempt(tmp_path: Path) -> None:
    (tmp_path / "city").mkdir()
    result = _transport(tmp_path).execute(_request())
    assert result.status == "execution_failed"
    assert "transport failure" in result.failure_detail


def test_worker_that_never_produces_a_result_fails_closed(tmp_path: Path) -> None:
    """A silent worker is a failed attempt, never an accepted one."""
    (tmp_path / "city").mkdir()
    transport = _transport(
        tmp_path,
        gc_binary=tmp_path / "gc-stub",
        worker_argv=(sys.executable, "-c", "raise SystemExit(0)"),
    )
    # Stub out submission so only the worker path is under test.
    transport._submit = lambda request: "job-1"  # type: ignore[method-assign]
    transport._close = lambda job_id, outcome: None  # type: ignore[method-assign]
    result = transport.execute(_request())
    assert result.status == "execution_failed"
    assert "without producing a result" in result.failure_detail


def test_worker_timeout_is_bounded_and_fails_closed(tmp_path: Path) -> None:
    (tmp_path / "city").mkdir()
    transport = _transport(
        tmp_path,
        worker_argv=(sys.executable, "-c", "import time; time.sleep(30)"),
        timeout=1.0,
    )
    transport._submit = lambda request: "job-1"  # type: ignore[method-assign]
    transport._close = lambda job_id, outcome: None  # type: ignore[method-assign]
    result = transport.execute(_request())
    assert result.status == "execution_failed"
    assert "timeout" in result.failure_detail


@pytest.mark.parametrize(
    "envelope_patch,match",
    [
        ({"mission_id": "mission-bbbbbbbbbbbb-99998888"}, "not this attempt"),
        ({"task_id": "other-task"}, "not this attempt"),
        ({"attempt": 7}, "not this attempt"),
        ({"attempt": "²"}, "unusable attempt"),
        ({"attempt": None}, "unusable attempt"),
        ({"baseline_sha": "f" * 40}, "different baseline"),
        ({"schema_version": 2}, "unsupported schema"),
        ({"nonce": "not-the-one"}, "does not answer this request"),
    ],
)
def test_wrong_identity_result_envelope_is_rejected(
    tmp_path: Path, envelope_patch: dict[str, Any], match: str
) -> None:
    """A result that is not this attempt's answer is never accepted."""
    (tmp_path / "city").mkdir()
    request = _request(nonce="the-real-nonce")
    path = tmp_path / "result.json"
    body: dict[str, Any] = {
        "schema_version": 1,
        "mission_id": request.mission_id,
        "task_id": request.task_id,
        "attempt": request.attempt,
        "baseline_sha": request.baseline_sha,
        "nonce": request.nonce,
        "result": result_to_dict(_result()),
    }
    body.update(envelope_patch)
    path.write_text(json.dumps(body))
    with pytest.raises(TransportError, match=match):
        _transport(tmp_path)._consume(request, path)


def test_a_correct_envelope_is_accepted(tmp_path: Path) -> None:
    (tmp_path / "city").mkdir()
    request = _request(nonce="the-real-nonce")
    path = tmp_path / "result.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "mission_id": request.mission_id,
                "task_id": request.task_id,
                "attempt": request.attempt,
                "baseline_sha": request.baseline_sha,
                "nonce": request.nonce,
                "result": result_to_dict(_result()),
            }
        )
    )
    assert _transport(tmp_path)._consume(request, path).status == "accepted"


def test_an_accepted_result_must_carry_a_real_new_candidate() -> None:
    """"Accepted" with no commit, or with the baseline itself, is refused."""
    request = _request()
    with pytest.raises(TransportError, match="must carry a candidate"):
        verify_result_identity(request, _result(candidate_sha=None))
    with pytest.raises(TransportError, match="cannot name the baseline"):
        verify_result_identity(request, _result(candidate_sha=request.baseline_sha))
    # A genuine new commit is fine.
    verify_result_identity(request, _result(candidate_sha="c" * 40))


def test_a_stale_result_file_cannot_answer_a_new_request(tmp_path: Path) -> None:
    """Each attempt gets a fresh nonce, so yesterday's file is not an answer."""
    (tmp_path / "city").mkdir()
    old = _request(nonce="old-nonce")
    path = tmp_path / "result.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "mission_id": old.mission_id,
                "task_id": old.task_id,
                "attempt": old.attempt,
                "baseline_sha": old.baseline_sha,
                "nonce": old.nonce,
                "result": result_to_dict(_result()),
            }
        )
    )
    fresh = _request(nonce="new-nonce")
    with pytest.raises(TransportError, match="does not answer this request"):
        _transport(tmp_path)._consume(fresh, path)


@pytest.mark.parametrize("bad", ["../escape", "mission/../../etc", "a b", ""])
def test_unsafe_identities_cannot_build_job_paths(tmp_path: Path, bad: str) -> None:
    (tmp_path / "city").mkdir()
    request = _request()
    object.__setattr__(request, "mission_id", bad)
    result = _transport(tmp_path).execute(request)
    assert result.status == "execution_failed"
    assert "unsafe" in result.failure_detail


def test_in_process_transport_preserves_the_gate_a_path(tmp_path: Path) -> None:
    """The default path runs the real task runner with no external dependency."""

    class Recorder:
        def __init__(self) -> None:
            self.calls: list[Any] = []

        def run_task(self, task, decision, specialist, workspace, **kwargs: Any):  # type: ignore[no-untyped-def]
            self.calls.append((task.id, decision.specialist_id, kwargs))
            return _result()

    recorder = Recorder()
    transport = InProcessTaskTransport(recorder)  # type: ignore[arg-type]
    request = _request(remediation_summary="fix the failing test")
    result = transport.execute(request)
    assert result.status == "accepted"
    task_id, specialist_id, kwargs = recorder.calls[0]
    assert task_id == "arith-code" and specialist_id == "python_backend"
    assert kwargs["remediation_summary"] == "fix the failing test"


@pytest.mark.parametrize(
    "workspace_dir",
    ["../../../etc", "a/../../..", "/absolute", "ws/../..", "", "with space", "..", "a/b"],
)
def test_forged_workspace_reference_is_refused(workspace_dir: str) -> None:
    """A request names WHICH workspace; it can never name where to look."""
    with pytest.raises(TransportError, match="unsafe workspace reference"):
        _request(workspace_dir=workspace_dir)


def test_workspaces_root_must_be_absolute() -> None:
    with pytest.raises(TransportError, match="absolute path"):
        _request(workspaces_root="relative/root")


def test_workspace_resolves_inside_the_configured_root(tmp_path: Path) -> None:
    request = _request(workspaces_root=str(tmp_path), workspace_dir="ws-1")
    assert request.workspace().path == tmp_path.resolve() / "ws-1"


def test_a_symlinked_workspace_cannot_escape_the_root(tmp_path: Path) -> None:
    """Even a valid NAME must still resolve inside the root."""
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "work"
    root.mkdir()
    (root / "sneaky").symlink_to(outside)
    with pytest.raises(TransportError, match="escapes the workspaces root"):
        _request(workspaces_root=str(root), workspace_dir="sneaky").workspace()
