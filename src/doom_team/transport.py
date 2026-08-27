"""Task execution transport: the seam between the mission and where work runs.

The mission lifecycle does not care WHERE a task attempt executes, only that
exactly one attempt produces exactly one canonical
:class:`~doom_team.task_runner.TaskExecutionResult`. This module defines that
seam and the in-process implementation of it; Gas City is another
implementation behind the same protocol.

What crosses the seam is deliberately narrow. A request carries the identity
of one attempt, the frontier it is pinned to, the workspace it must use, the
pinned task contract and routing decision, and a bounded correction context -
nothing else. It never carries credentials, and it never carries executable
commands: the validation policy travels as a NAME that the executing side
resolves from its own trusted registry, so a request can never ask a worker
to run something of its choosing.

Doom Team remains the brain on both sides of the seam. Planning, the DAG,
routing, the attempt budget, the recovery decision, the frontier, and human
approval all stay with the mission layer; a transport only executes one
attempt and hands back what happened.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from doom_team.deliverable import TaskWorkspace
from doom_team.domain import TaskContract
from doom_team.routing import RoutingDecision, SpecialistProfile, specialist_by_id
from doom_team.task_runner import (
    STATUS_ACCEPTED,
    STATUS_EXECUTION_FAILED,
    ProductionTaskRunner,
    RoleVerdict,
    TaskExecutionResult,
)
from doom_team.validation import CommandResult, ValidationCommand, ValidationResult

MAX_ENVELOPE_TEXT_CHARS = 8_000

# A workspace reference is one generated directory NAME, never a path. Anything
# else - a traversal, a separator, an absolute path - would point the attempt
# at a directory nobody chose.
_SAFE_DIR_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$")

# Validation plans are TRUSTED configuration, never request data. A request
# names a policy; the executing side resolves the name here. An unknown name
# fails closed rather than degrading to "run nothing".
VALIDATION_POLICIES: dict[str, tuple[ValidationCommand, ...]] = {
    "default": (
        ValidationCommand(label="compile", argv=(sys.executable, "-m", "compileall", "-q", ".")),
        ValidationCommand(label="tests", argv=(sys.executable, "-m", "pytest", "-q")),
    ),
    "compile-only": (
        ValidationCommand(label="compile", argv=(sys.executable, "-m", "compileall", "-q", ".")),
    ),
}


class TransportError(Exception):
    """Raised when a transport cannot deliver a trustworthy result."""


def resolve_validation_policy(name: str) -> tuple[ValidationCommand, ...]:
    """Resolve a named validation plan from trusted configuration."""
    try:
        return VALIDATION_POLICIES[name]
    except KeyError:
        raise TransportError(f"unknown validation policy: {name!r}") from None


def _clip(text: str) -> str:
    return text if len(text) <= MAX_ENVELOPE_TEXT_CHARS else text[:MAX_ENVELOPE_TEXT_CHARS]


@dataclass(frozen=True)
class TaskAttemptRequest:
    """One task attempt, described completely and carrying nothing else."""

    mission_id: str
    task_id: str
    attempt: int
    baseline_sha: str
    workspace_dir: str
    branch: str
    workspaces_root: str
    task: TaskContract
    decision: RoutingDecision
    validation_policy: str = "default"
    # Minted per attempt by the transport and echoed back in the result. It
    # does not make a worker trustworthy - the worker is our own code - but it
    # binds a result to THIS request, so a file that merely appears in the job
    # directory cannot pass as one.
    nonce: str = ""
    objective_summary: str = ""
    dependency_summary: str = ""
    remediation_summary: str = ""

    def __post_init__(self) -> None:
        if self.attempt < 1:
            raise TransportError("attempt numbers start at 1")
        if self.task.id != self.task_id:
            raise TransportError("request task id does not match the task contract")
        if self.decision.task_id != self.task_id:
            raise TransportError("request task id does not match the routing decision")
        if not self.baseline_sha:
            raise TransportError("a task attempt must be pinned to a baseline")
        if not _SAFE_DIR_NAME.fullmatch(self.workspace_dir) or ".." in self.workspace_dir:
            raise TransportError(f"unsafe workspace reference: {self.workspace_dir!r}")
        if not Path(self.workspaces_root).is_absolute():
            raise TransportError("workspaces root must be an absolute path")

    @property
    def identity(self) -> tuple[str, str, int]:
        return (self.mission_id, self.task_id, self.attempt)

    def workspace(self) -> TaskWorkspace:
        """Rebuild the workspace handle this attempt must use, fail closed.

        The workspace must resolve inside the configured workspaces root: a
        request may say WHICH workspace, never where the filesystem it lives
        on begins.
        """
        root = Path(self.workspaces_root).resolve()
        path = (root / self.workspace_dir).resolve()
        if path != root and root not in path.parents:
            raise TransportError(
                f"workspace {self.workspace_dir!r} escapes the workspaces root"
            )
        return TaskWorkspace(
            task_id=self.task_id,
            path=root / self.workspace_dir,
            baseline_sha=self.baseline_sha,
            branch=self.branch,
        )

    def specialist(self) -> SpecialistProfile:
        return specialist_by_id(self.decision.specialist_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "mission_id": self.mission_id,
            "task_id": self.task_id,
            "attempt": self.attempt,
            "baseline_sha": self.baseline_sha,
            "workspace_dir": self.workspace_dir,
            "branch": self.branch,
            "workspaces_root": self.workspaces_root,
            "validation_policy": self.validation_policy,
            "nonce": self.nonce,
            "task": {
                "id": self.task.id,
                "objective_id": self.task.objective_id,
                "instruction": self.task.instruction,
                "acceptance_criteria": list(self.task.acceptance_criteria),
                "dependencies": list(self.task.dependencies),
                "required_capabilities": list(self.task.required_capabilities),
            },
            "decision": self.decision.as_dict(),
            "objective_summary": _clip(self.objective_summary),
            "dependency_summary": _clip(self.dependency_summary),
            "remediation_summary": _clip(self.remediation_summary),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskAttemptRequest":
        if data.get("schema_version") != 1:
            raise TransportError(f"unsupported request schema: {data.get('schema_version')!r}")
        try:
            raw_task = data["task"]
            raw_decision = data["decision"]
            task = TaskContract(
                id=str(raw_task["id"]),
                objective_id=str(raw_task["objective_id"]),
                instruction=str(raw_task["instruction"]),
                acceptance_criteria=tuple(str(c) for c in raw_task["acceptance_criteria"]),
                dependencies=tuple(str(d) for d in raw_task.get("dependencies", ())),
                required_capabilities=tuple(
                    str(c) for c in raw_task.get("required_capabilities", ())
                ),
            )
            decision = RoutingDecision(
                task_id=str(raw_decision["task_id"]),
                required_capabilities=tuple(
                    str(c) for c in raw_decision["required_capabilities"]
                ),
                specialist_id=str(raw_decision["specialist_id"]),
                rationale=str(raw_decision["rationale"]),
            )
            return cls(
                mission_id=str(data["mission_id"]),
                task_id=str(data["task_id"]),
                attempt=int(data["attempt"]),
                baseline_sha=str(data["baseline_sha"]),
                workspace_dir=str(data["workspace_dir"]),
                branch=str(data["branch"]),
                workspaces_root=str(data["workspaces_root"]),
                task=task,
                decision=decision,
                validation_policy=str(data.get("validation_policy", "default")),
                nonce=str(data.get("nonce", "")),
                objective_summary=str(data.get("objective_summary", "")),
                dependency_summary=str(data.get("dependency_summary", "")),
                remediation_summary=str(data.get("remediation_summary", "")),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise TransportError(f"malformed task attempt request: {exc}") from exc


def result_to_dict(result: TaskExecutionResult) -> dict[str, Any]:
    """Serialize a canonical result without inventing a second result model."""
    return {
        "task_id": result.task_id,
        "specialist_id": result.specialist_id,
        "baseline_sha": result.baseline_sha,
        "status": result.status,
        "candidate_sha": result.candidate_sha,
        "changed_files": list(result.changed_files),
        "implementation_summary": result.implementation_summary,
        "validation_results": [
            {
                "label": item.label,
                "command": {
                    "argv": list(item.command.argv),
                    "cwd": item.command.cwd,
                    "exit_code": item.command.exit_code,
                    "stdout": item.command.stdout,
                    "stderr": item.command.stderr,
                    "duration_seconds": item.command.duration_seconds,
                    "timed_out": item.command.timed_out,
                    "stdout_truncated": item.command.stdout_truncated,
                    "stderr_truncated": item.command.stderr_truncated,
                },
            }
            for item in result.validation_results
        ],
        "qa": result.qa.as_dict() if result.qa is not None else None,
        "reviewer": result.reviewer.as_dict() if result.reviewer is not None else None,
        "failure_detail": result.failure_detail,
    }


def _verdict_from_dict(data: Any, role: str) -> RoleVerdict | None:
    if data is None:
        return None
    if not isinstance(data, dict):
        raise TransportError(f"malformed {role} verdict")
    verdict = str(data.get("verdict", ""))
    if verdict not in ("pass", "block"):
        raise TransportError(f"{role} verdict is not a recognized decision: {verdict!r}")
    return RoleVerdict(
        role=str(data.get("role", role)),
        verdict=verdict,
        summary=str(data.get("summary", "")),
        blockers=tuple(str(b) for b in data.get("blockers", ())),
    )


def result_from_dict(data: dict[str, Any]) -> TaskExecutionResult:
    """Rebuild a canonical result, deriving pass/fail from real exit codes.

    ``passed`` is never read from the payload: it is recomputed by
    :class:`~doom_team.validation.ValidationResult` from the recorded command
    exit code, so a serialized result cannot assert a green run that the
    recorded execution contradicts.
    """
    try:
        validation: list[ValidationResult] = []
        for item in data.get("validation_results", ()):
            command = item["command"]
            validation.append(
                ValidationResult(
                    label=str(item["label"]),
                    command=CommandResult(
                        argv=tuple(str(a) for a in command["argv"]),
                        cwd=str(command["cwd"]),
                        exit_code=int(command["exit_code"]),
                        stdout=str(command.get("stdout", "")),
                        stderr=str(command.get("stderr", "")),
                        duration_seconds=float(command.get("duration_seconds", 0.0)),
                        timed_out=bool(command.get("timed_out", False)),
                        stdout_truncated=bool(command.get("stdout_truncated", False)),
                        stderr_truncated=bool(command.get("stderr_truncated", False)),
                    ),
                )
            )
        return TaskExecutionResult(
            task_id=str(data["task_id"]),
            specialist_id=str(data["specialist_id"]),
            baseline_sha=str(data["baseline_sha"]),
            status=str(data["status"]),
            candidate_sha=(
                str(data["candidate_sha"]) if data.get("candidate_sha") else None
            ),
            changed_files=tuple(str(f) for f in data.get("changed_files", ())),
            implementation_summary=str(data.get("implementation_summary", "")),
            validation_results=tuple(validation),
            qa=_verdict_from_dict(data.get("qa"), "qa"),
            reviewer=_verdict_from_dict(data.get("reviewer"), "reviewer"),
            failure_detail=str(data.get("failure_detail", "")),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise TransportError(f"malformed task execution result: {exc}") from exc


def verify_result_identity(
    request: TaskAttemptRequest, result: TaskExecutionResult
) -> None:
    """Refuse any result that is not this attempt's, fail closed.

    A transport can deliver the wrong payload in several ways - a replayed
    result from an earlier attempt, another task's result, a result built on a
    different baseline. None of those may be applied to this attempt.
    """
    if result.task_id != request.task_id:
        raise TransportError(
            f"result belongs to task {result.task_id!r}, not {request.task_id!r}"
        )
    if result.baseline_sha != request.baseline_sha:
        raise TransportError(
            f"result was produced against baseline {result.baseline_sha}, "
            f"not the pinned {request.baseline_sha}"
        )
    if result.specialist_id != request.decision.specialist_id:
        raise TransportError(
            f"result came from specialist {result.specialist_id!r}, "
            f"not the routed {request.decision.specialist_id!r}"
        )
    if result.status == STATUS_ACCEPTED:
        # Accepting work means a candidate commit exists that is not simply
        # the baseline. Otherwise "integration" would advance the frontier to
        # where it already is, and a task would land having produced nothing.
        if not result.candidate_sha:
            raise TransportError("an accepted result must carry a candidate commit")
        if result.candidate_sha == request.baseline_sha:
            raise TransportError(
                "an accepted result cannot name the baseline as its candidate"
            )


def transport_failure(request: TaskAttemptRequest, detail: str) -> TaskExecutionResult:
    """A transport failure is a failed ATTEMPT, never a silent success.

    It is reported in the canonical vocabulary so the mission layer counts it
    against the same bounded budget as any other terminal attempt; the
    transport itself never retries.
    """
    return TaskExecutionResult(
        task_id=request.task_id,
        specialist_id=request.decision.specialist_id,
        baseline_sha=request.baseline_sha,
        status=STATUS_EXECUTION_FAILED,
        failure_detail=f"transport failure: {detail}"[:2000],
    )


@runtime_checkable
class TaskExecutionTransport(Protocol):
    """Executes exactly one task attempt and returns its canonical result."""

    def execute(self, request: TaskAttemptRequest) -> TaskExecutionResult:
        """Run one attempt. Never retries; never returns another attempt's result."""


class InProcessTaskTransport:
    """The Gate-A path: run the attempt right here, in this process."""

    def __init__(self, runner: ProductionTaskRunner) -> None:
        self._runner = runner

    def execute(self, request: TaskAttemptRequest) -> TaskExecutionResult:
        return self._runner.run_task(
            request.task,
            request.decision,
            request.specialist(),
            request.workspace(),
            objective_summary=request.objective_summary,
            dependency_summary=request.dependency_summary,
            remediation_summary=request.remediation_summary,
        )
