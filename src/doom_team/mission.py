"""Durable mission ledger: the persistent truth of one mission execution.

A mission run is the whole arc from a natural-language objective to a
deliverable frontier awaiting explicit human approval. This module owns the
durable record of that arc and nothing else: it does not execute work, call
inference, or touch Git. :class:`~doom_team.mission_runner.MissionRunner`
drives execution and reports here.

STATE TRUTH. The ledger has no ``update(dict)`` seam. Every transition is a
narrow method that takes the real runtime object it is recording - a
:class:`~doom_team.domain.Mission`, a
:class:`~doom_team.routing.RoutingDecision`, a
:class:`~doom_team.task_runner.TaskExecutionResult`, a
:class:`~doom_team.validation.ValidationResult` - and derives the stored state
from it. A caller cannot assert that a task was accepted, that validation
passed, or that QA and the Reviewer approved: those are read out of the
recorded objects, and :meth:`MissionStore.record_task_result` re-checks the
whole T4 chain before it will store ``accepted``. Likewise a mission can only
reach ``awaiting_human_approval`` when every planned task is integrated and a
fresh final validation is recorded green - the ledger recomputes that from its
own stored state rather than trusting a flag.

Records are written through :mod:`doom_team.durable`, so every file is
atomic, checksummed, and fails closed when tampered with. Credentials are
never stored: configured secret values are redacted from every string written.

Recovery, retry, and resume are deliberately absent; they belong to a later
layer. An interrupted mission simply stays ``running`` and is reported as
incomplete when inspected.
"""

from __future__ import annotations

import datetime
import fcntl
import os
import re
import secrets
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from collections.abc import Callable, Iterator
from typing import Any

from doom_team.domain import Mission, TaskContract
from doom_team.durable import atomic_write_json, read_json_checked
from doom_team.routing import RoutingDecision
from doom_team.task_runner import (
    STATUS_ACCEPTED,
    STATUS_EXECUTION_FAILED,
    STATUS_QA_BLOCKED,
    STATUS_REVIEWER_BLOCKED,
    STATUS_VALIDATION_FAILED,
    TaskExecutionResult,
)
from doom_team.validation import ValidationResult

SCHEMA_VERSION = 1

# Mission statuses. `running` covers an in-flight (or interrupted) mission;
# every other value is terminal for this layer, since T5 never retries.
MISSION_RUNNING = "running"
MISSION_BLOCKED = "blocked"
MISSION_FINAL_VALIDATION_FAILED = "final_validation_failed"
MISSION_AWAITING_HUMAN_APPROVAL = "awaiting_human_approval"
MISSION_APPROVED = "approved"
MISSION_REJECTED = "rejected"

# Task states. Failure states reuse the T4 status vocabulary verbatim rather
# than inventing a parallel one.
TASK_PENDING = "pending"
TASK_RUNNING = "running"
TASK_ACCEPTED = "accepted"
TASK_INTEGRATED = "integrated"

# The only non-accepted outcomes a real task execution can report. Anything
# else is refused rather than stored verbatim, so a result can never name a
# privileged state (``integrated``) that only the ledger is allowed to assign.
TASK_FAILURE_STATES = (
    STATUS_VALIDATION_FAILED,
    STATUS_QA_BLOCKED,
    STATUS_REVIEWER_BLOCKED,
    STATUS_EXECUTION_FAILED,
)

# An attempt whose process died before any trusted terminal result existed.
# It is terminal for that attempt and it CONSUMES budget, so crashing is never
# a way to buy extra tries.
ATTEMPT_RUNNING = TASK_RUNNING
ATTEMPT_INTERRUPTED = "interrupted"

# Outcomes a fresh bounded attempt may follow. Every other terminal state -
# a safety violation, ambiguous provenance, corrupt state - stays terminal.
RECOVERABLE_STATES = TASK_FAILURE_STATES + (ATTEMPT_INTERRUPTED,)

# Ledger evidence bounds: enough to understand what happened, never a second
# copy of the repository. Diffs are referenced by SHA, never stored.
MAX_LEDGER_OUTPUT_CHARS = 4_000
MAX_LEDGER_TEXT_CHARS = 2_000
MAX_LEDGER_BLOCKERS = 20
MAX_REMEDIATION_CHARS = 2_000

_MISSION_ID = re.compile(r"^mission-[0-9a-f]{6,32}-[0-9a-f]{8}$")

# Reserved ApprovalPort action namespace for mission decisions.
MISSION_ACTION_PREFIX = "mission:"

# The size of the bound is the project's existing recovery policy: a task gets
# one initial attempt plus `Settings.semantic_retries` remediations, and this
# default mirrors the canonical Settings default. The metering is deliberately
# STRICTER than doom_team.recovery's, which exempts runtime failures from the
# semantic budget: here every terminal attempt occupies a slot, including a
# provider failure or an interrupted one. A mission must not be able to retry
# forever because the failures keep being classified as infrastructural.
DEFAULT_SEMANTIC_RETRIES = 3


def max_attempts_for(semantic_retries: int) -> int:
    """One initial attempt plus the configured semantic remediation budget."""
    if semantic_retries < 0:
        raise MissionLedgerError("semantic retries cannot be negative")
    return 1 + semantic_retries


DEFAULT_MAX_ATTEMPTS_PER_TASK = 1 + DEFAULT_SEMANTIC_RETRIES


class MissionLedgerError(Exception):
    """Raised when a mission state transition is invalid (fail closed)."""


def new_mission_id(plan_mission_id: str) -> str:
    """Mint a durable identity for one mission RUN.

    The planner's mission id is a digest of the objective, so it repeats for
    a repeated objective. A run needs its own identity: the plan digest keeps
    the id connected to what was planned, and the random suffix keeps
    concurrent and historical runs of the same objective distinct. The result
    is lowercase hex and dashes only - filesystem-safe, carrying no objective
    text and no credentials.
    """
    digest = "".join(ch for ch in plan_mission_id if ch in "0123456789abcdef")[:32]
    if len(digest) < 6:
        raise MissionLedgerError(f"plan mission id has no usable digest: {plan_mission_id!r}")
    return f"mission-{digest}-{secrets.token_hex(4)}"


def _utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _clip(text: str, limit: int = MAX_LEDGER_TEXT_CHARS) -> str:
    return text if len(text) <= limit else text[:limit]


def _redact_text(text: str, secret_values: tuple[str, ...]) -> str:
    for secret in secret_values:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    return text


# Lookup keys are never rewritten: redacting an identifier would corrupt the
# record's own addressing. Plans whose identifiers carry secret material are
# refused outright in :meth:`MissionStore.record_plan` instead.
_IDENTITY_KEYS = frozenset({"mission_id", "task_id", "workspace_dir", "branch"})


def _redact(value: Any, secret_values: tuple[str, ...]) -> Any:
    """Recursively remove configured secret values from anything stored."""
    if not secret_values:
        return value
    if isinstance(value, str):
        return _redact_text(value, secret_values)
    if isinstance(value, dict):
        return {
            k: v if k in _IDENTITY_KEYS else _redact(v, secret_values)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redact(v, secret_values) for v in value]
    return value


@dataclass(frozen=True)
class MissionAttemptRecord:
    """One immutable execution attempt at a task.

    Attempts are append-only history. A later successful recovery is a NEW
    attempt; it never rewrites the failed one, so "attempt 1 failed" stays
    true forever.
    """

    attempt: int
    baseline_sha: str
    specialist_id: str
    started_at: str
    ended_at: str = ""
    status: str = ATTEMPT_RUNNING
    candidate_sha: str = ""
    changed_files: tuple[str, ...] = ()
    implementation_summary: str = ""
    validation: tuple[dict[str, Any], ...] = ()
    qa: dict[str, Any] | None = None
    reviewer: dict[str, Any] | None = None
    failure_detail: str = ""
    remediation: str = ""
    # Only the generated leaf directory name and branch, never an absolute
    # path: enough for a resumed process to find the workspace under its own
    # configured root, without persisting an operator path.
    workspace_dir: str = ""
    branch: str = ""

    @property
    def terminal(self) -> bool:
        return self.status != ATTEMPT_RUNNING

    @property
    def accepted(self) -> bool:
        return self.status == STATUS_ACCEPTED

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempt": self.attempt,
            "baseline_sha": self.baseline_sha,
            "specialist_id": self.specialist_id,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "status": self.status,
            "candidate_sha": self.candidate_sha,
            "changed_files": list(self.changed_files),
            "implementation_summary": self.implementation_summary,
            "validation": [dict(i) for i in self.validation],
            "qa": dict(self.qa) if self.qa is not None else None,
            "reviewer": dict(self.reviewer) if self.reviewer is not None else None,
            "failure_detail": self.failure_detail,
            "remediation": self.remediation,
            "workspace_dir": self.workspace_dir,
            "branch": self.branch,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MissionAttemptRecord":
        return cls(
            attempt=int(data["attempt"]),
            baseline_sha=str(data.get("baseline_sha", "")),
            specialist_id=str(data.get("specialist_id", "")),
            started_at=str(data.get("started_at", "")),
            ended_at=str(data.get("ended_at", "")),
            status=str(data.get("status", ATTEMPT_RUNNING)),
            candidate_sha=str(data.get("candidate_sha", "")),
            changed_files=tuple(str(f) for f in data.get("changed_files", ())),
            implementation_summary=str(data.get("implementation_summary", "")),
            validation=tuple(dict(i) for i in data.get("validation", ())),
            qa=dict(data["qa"]) if data.get("qa") is not None else None,
            reviewer=dict(data["reviewer"]) if data.get("reviewer") is not None else None,
            failure_detail=str(data.get("failure_detail", "")),
            remediation=str(data.get("remediation", "")),
            workspace_dir=str(data.get("workspace_dir", "")),
            branch=str(data.get("branch", "")),
        )


@dataclass(frozen=True)
class MissionTaskRecord:
    """Durable record of one task inside a mission."""

    task_id: str
    dependencies: tuple[str, ...] = ()
    required_capabilities: tuple[str, ...] = ()
    # The pinned contract, so a resumed mission rebuilds exactly the task that
    # was planned instead of asking the planner again.
    instruction: str = ""
    acceptance_criteria: tuple[str, ...] = ()
    attempts: tuple[MissionAttemptRecord, ...] = ()
    specialist_id: str = ""
    state: str = TASK_PENDING
    baseline_sha: str = ""
    candidate_sha: str = ""
    changed_files: tuple[str, ...] = ()
    implementation_summary: str = ""
    validation: tuple[dict[str, Any], ...] = ()
    qa: dict[str, Any] | None = None
    reviewer: dict[str, Any] | None = None
    failure_detail: str = ""
    integrated_sha: str = ""

    @property
    def accepted(self) -> bool:
        return self.state in (TASK_ACCEPTED, TASK_INTEGRATED)

    @property
    def integrated(self) -> bool:
        return self.state == TASK_INTEGRATED

    @property
    def open_attempt(self) -> MissionAttemptRecord | None:
        """The attempt still in flight, if any (at most one by construction)."""
        for attempt in self.attempts:
            if not attempt.terminal:
                return attempt
        return None

    @property
    def accepted_attempt(self) -> MissionAttemptRecord | None:
        for attempt in self.attempts:
            if attempt.accepted:
                return attempt
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "dependencies": list(self.dependencies),
            "required_capabilities": list(self.required_capabilities),
            "instruction": self.instruction,
            "acceptance_criteria": list(self.acceptance_criteria),
            "attempts": [a.to_dict() for a in self.attempts],
            "specialist_id": self.specialist_id,
            "state": self.state,
            "baseline_sha": self.baseline_sha,
            "candidate_sha": self.candidate_sha,
            "changed_files": list(self.changed_files),
            "implementation_summary": self.implementation_summary,
            "validation": [dict(item) for item in self.validation],
            "qa": dict(self.qa) if self.qa is not None else None,
            "reviewer": dict(self.reviewer) if self.reviewer is not None else None,
            "failure_detail": self.failure_detail,
            "integrated_sha": self.integrated_sha,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MissionTaskRecord":
        return cls(
            task_id=str(data["task_id"]),
            dependencies=tuple(str(d) for d in data.get("dependencies", ())),
            required_capabilities=tuple(str(c) for c in data.get("required_capabilities", ())),
            instruction=str(data.get("instruction", "")),
            acceptance_criteria=tuple(str(c) for c in data.get("acceptance_criteria", ())),
            attempts=tuple(
                MissionAttemptRecord.from_dict(a) for a in data.get("attempts", ())
            ),
            specialist_id=str(data.get("specialist_id", "")),
            state=str(data.get("state", TASK_PENDING)),
            baseline_sha=str(data.get("baseline_sha", "")),
            candidate_sha=str(data.get("candidate_sha", "")),
            changed_files=tuple(str(f) for f in data.get("changed_files", ())),
            implementation_summary=str(data.get("implementation_summary", "")),
            validation=tuple(dict(item) for item in data.get("validation", ())),
            qa=dict(data["qa"]) if data.get("qa") is not None else None,
            reviewer=dict(data["reviewer"]) if data.get("reviewer") is not None else None,
            failure_detail=str(data.get("failure_detail", "")),
            integrated_sha=str(data.get("integrated_sha", "")),
        )


@dataclass(frozen=True)
class MissionRecord:
    """The complete durable state of one mission run."""

    mission_id: str
    objective: str
    objective_id: str
    plan_mission_id: str
    deliverable_path: str
    created_at: str
    updated_at: str
    status: str
    baseline_sha: str = ""
    frontier_sha: str = ""
    final_frontier_sha: str = ""
    final_validation_sha: str = ""
    max_attempts_per_task: int = 0
    # Which transport executed this mission's attempts. Recorded so a later
    # resume cannot quietly run the rest of the work somewhere else.
    task_transport: str = ""
    tasks: tuple[MissionTaskRecord, ...] = ()
    routing: tuple[dict[str, Any], ...] = ()
    final_validation: tuple[dict[str, Any], ...] | None = None
    blocker: dict[str, Any] | None = None
    approval: dict[str, Any] | None = None
    schema_version: int = SCHEMA_VERSION

    def task(self, task_id: str) -> MissionTaskRecord:
        for record in self.tasks:
            if record.task_id == task_id:
                return record
        raise MissionLedgerError(f"mission {self.mission_id} has no task {task_id}")

    @property
    def all_tasks_integrated(self) -> bool:
        return bool(self.tasks) and all(task.integrated for task in self.tasks)

    @property
    def final_validation_passed(self) -> bool:
        results = self.final_validation
        if not results:
            return False
        return all(bool(item.get("passed")) for item in results)

    @property
    def approval_required(self) -> bool:
        return self.status == MISSION_AWAITING_HUMAN_APPROVAL

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "mission_id": self.mission_id,
            "objective": self.objective,
            "objective_id": self.objective_id,
            "plan_mission_id": self.plan_mission_id,
            "deliverable_path": self.deliverable_path,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "status": self.status,
            "baseline_sha": self.baseline_sha,
            "frontier_sha": self.frontier_sha,
            "final_frontier_sha": self.final_frontier_sha,
            "final_validation_sha": self.final_validation_sha,
            "max_attempts_per_task": self.max_attempts_per_task,
            "task_transport": self.task_transport,
            "tasks": [task.to_dict() for task in self.tasks],
            "routing": [dict(item) for item in self.routing],
            "final_validation": (
                None if self.final_validation is None else [dict(i) for i in self.final_validation]
            ),
            "blocker": dict(self.blocker) if self.blocker is not None else None,
            "approval": dict(self.approval) if self.approval is not None else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MissionRecord":
        version = data.get("schema_version")
        if version != SCHEMA_VERSION:
            raise MissionLedgerError(f"unsupported mission schema version: {version!r}")
        final_validation = data.get("final_validation")
        return cls(
            mission_id=str(data["mission_id"]),
            objective=str(data["objective"]),
            objective_id=str(data.get("objective_id", "")),
            plan_mission_id=str(data.get("plan_mission_id", "")),
            deliverable_path=str(data.get("deliverable_path", "")),
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
            status=str(data.get("status", MISSION_RUNNING)),
            baseline_sha=str(data.get("baseline_sha", "")),
            frontier_sha=str(data.get("frontier_sha", "")),
            final_frontier_sha=str(data.get("final_frontier_sha", "")),
            final_validation_sha=str(data.get("final_validation_sha", "")),
            # A record written before the bound existed has none; adopt the
            # canonical default rather than reading it as "no attempts left".
            max_attempts_per_task=(
                int(data.get("max_attempts_per_task") or DEFAULT_MAX_ATTEMPTS_PER_TASK)
            ),
            task_transport=str(data.get("task_transport", "")),
            tasks=tuple(MissionTaskRecord.from_dict(t) for t in data.get("tasks", ())),
            routing=tuple(dict(item) for item in data.get("routing", ())),
            final_validation=(
                None if final_validation is None else tuple(dict(i) for i in final_validation)
            ),
            blocker=dict(data["blocker"]) if data.get("blocker") is not None else None,
            approval=dict(data["approval"]) if data.get("approval") is not None else None,
        )


def _bounded_validation(
    results: tuple[ValidationResult, ...], safe: Callable[[str, int], str]
) -> tuple[dict[str, Any], ...]:
    """Bounded, secret-free evidence for the ledger.

    ``passed`` comes from :class:`~doom_team.validation.ValidationResult`,
    where it is derived from a real command exit code - there is no field a
    caller could set to claim a green run.
    """
    bounded: list[dict[str, Any]] = []
    for result in results:
        evidence = result.as_evidence_dict()
        evidence["stdout"] = safe(str(evidence.get("stdout", "")), MAX_LEDGER_OUTPUT_CHARS)
        evidence["stderr"] = safe(str(evidence.get("stderr", "")), MAX_LEDGER_OUTPUT_CHARS)
        bounded.append(evidence)
    return tuple(bounded)


def _bounded_verdict(verdict: Any, safe: Callable[[str, int], str]) -> dict[str, Any] | None:
    if verdict is None:
        return None
    payload: dict[str, Any] = dict(verdict.as_dict())
    payload["summary"] = safe(str(payload.get("summary", "")), MAX_LEDGER_TEXT_CHARS)
    blockers = [safe(str(b), MAX_LEDGER_TEXT_CHARS) for b in payload.get("blockers", [])][
        :MAX_LEDGER_BLOCKERS
    ]
    payload["blockers"] = blockers
    return payload


class MissionStore:
    """Durable, checksummed mission ledger with narrow recording methods."""

    def __init__(self, base_dir: str | Path, *, secret_values: tuple[str, ...] = ()) -> None:
        self.base_dir = Path(base_dir)
        self.missions_dir = self.base_dir / "missions"
        self.missions_dir.mkdir(parents=True, exist_ok=True)
        self._secret_values = tuple(s for s in secret_values if s)

    def _safe(self, text: str, limit: int = MAX_LEDGER_TEXT_CHARS) -> str:
        """Redact first, then bound.

        Order matters: clipping a credential in half would leave a prefix that
        no longer matches the secret, and the later whole-record redaction
        could not remove it.
        """
        return _clip(_redact_text(text, self._secret_values), limit)

    def _path(self, mission_id: str) -> Path:
        if not _MISSION_ID.fullmatch(mission_id):
            raise MissionLedgerError(f"unsafe mission id: {mission_id!r}")
        return self.missions_dir / f"{mission_id}.json"

    def _write(self, record: MissionRecord) -> MissionRecord:
        """Persist a record and return exactly what was persisted.

        Returning the redacted payload - not the in-memory original - keeps
        the value callers go on to print identical to the durable state, so a
        secret cannot survive in a result object that never touched disk.
        """
        stamped = replace(record, updated_at=_utc_now_iso())
        payload = _redact(stamped.to_dict(), self._secret_values)
        atomic_write_json(self._path(stamped.mission_id), payload)
        return MissionRecord.from_dict(payload)

    def exists(self, mission_id: str) -> bool:
        return self._path(mission_id).exists()

    def load(self, mission_id: str) -> MissionRecord:
        """Load a mission, failing closed on missing or corrupt state."""
        return MissionRecord.from_dict(read_json_checked(self._path(mission_id)))

    def list_ids(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                path.stem
                for path in self.missions_dir.glob("mission-*.json")
                if not path.stem.endswith((".execution", ".decision"))
            )
        )

    def create(
        self,
        *,
        mission_id: str,
        objective: str,
        plan_mission_id: str,
        objective_id: str,
        max_attempts_per_task: int = DEFAULT_MAX_ATTEMPTS_PER_TASK,
        task_transport: str = "",
    ) -> MissionRecord:
        """Create a brand-new mission record; never overwrites an existing one."""
        if self.exists(mission_id):
            raise MissionLedgerError(f"mission {mission_id} already exists")
        if max_attempts_per_task < 1:
            raise MissionLedgerError("a mission needs at least one attempt per task")
        now = _utc_now_iso()
        return self._write(
            MissionRecord(
                mission_id=mission_id,
                objective=self._safe(objective, MAX_LEDGER_OUTPUT_CHARS),
                objective_id=objective_id,
                plan_mission_id=plan_mission_id,
                deliverable_path="",
                created_at=now,
                updated_at=now,
                status=MISSION_RUNNING,
                max_attempts_per_task=max_attempts_per_task,
                task_transport=task_transport,
            )
        )

    def _require_running(self, record: MissionRecord) -> None:
        if record.status != MISSION_RUNNING:
            raise MissionLedgerError(
                f"mission {record.mission_id} is {record.status}, not running"
            )

    def record_plan(self, mission_id: str, mission: Mission) -> MissionRecord:
        """Record the validated plan; task rows come from the Mission itself."""
        record = self.load(mission_id)
        self._require_running(record)
        if record.tasks:
            raise MissionLedgerError(f"mission {mission_id} already has a plan")
        # Identifiers are lookup keys, so they are stored verbatim. A plan
        # whose ids carry secret material would either persist a credential
        # or be rewritten into an id the runtime can no longer find - refuse
        # it here, plainly, before any work starts.
        for task in mission.tasks:
            if _redact_text(task.id, self._secret_values) != task.id:
                raise MissionLedgerError(
                    f"planned task identifier {task.id!r} contains redacted content"
                )
        tasks = tuple(
            MissionTaskRecord(
                task_id=task.id,
                dependencies=task.dependencies,
                required_capabilities=task.required_capabilities,
                instruction=self._safe(task.instruction, MAX_LEDGER_OUTPUT_CHARS),
                acceptance_criteria=tuple(
                    self._safe(c) for c in task.acceptance_criteria
                ),
            )
            for task in mission.tasks
        )
        return self._write(replace(record, tasks=tasks))

    def record_deliverable(
        self, mission_id: str, *, deliverable_path: str, baseline_sha: str
    ) -> MissionRecord:
        """Record the deliverable repository and its starting frontier."""
        record = self.load(mission_id)
        self._require_running(record)
        return self._write(
            replace(
                record,
                deliverable_path=deliverable_path,
                baseline_sha=baseline_sha,
                frontier_sha=baseline_sha,
            )
        )

    def record_routing(
        self, mission_id: str, decisions: tuple[RoutingDecision, ...]
    ) -> MissionRecord:
        """Record the automatic routing decisions for every planned task."""
        record = self.load(mission_id)
        self._require_running(record)
        by_task = {decision.task_id: decision for decision in decisions}
        tasks = tuple(
            replace(task, specialist_id=by_task[task.task_id].specialist_id)
            if task.task_id in by_task
            else task
            for task in record.tasks
        )
        routing = tuple(decision.as_dict() for decision in decisions)
        return self._write(replace(record, tasks=tasks, routing=routing))

    def record_task_started(
        self,
        mission_id: str,
        task_id: str,
        *,
        baseline_sha: str,
        specialist_id: str = "",
        remediation: str = "",
        workspace_dir: str = "",
        branch: str = "",
    ) -> MissionRecord:
        """Open a bounded attempt against the frontier it actually starts from.

        The bound is enforced here, from durable history: a task may only open
        as many attempts as the mission's ``max_attempts_per_task``. Because
        the count comes from persisted attempts rather than a live counter,
        restarting the process cannot buy extra tries.
        """
        record = self.load(mission_id)
        self._require_running(record)
        task = record.task(task_id)
        if task.state == TASK_RUNNING or task.open_attempt is not None:
            raise MissionLedgerError(f"task {task_id} already has an attempt in flight")
        if task.state not in (TASK_PENDING,) + RECOVERABLE_STATES:
            raise MissionLedgerError(f"task {task_id} is {task.state}; it cannot be attempted")
        if len(task.attempts) >= record.max_attempts_per_task:
            raise MissionLedgerError(
                f"task {task_id} has used all {record.max_attempts_per_task} attempts"
            )
        if baseline_sha != record.frontier_sha:
            raise MissionLedgerError(
                f"task {task_id} would start from {baseline_sha}, "
                f"not the current frontier {record.frontier_sha}"
            )
        attempt = MissionAttemptRecord(
            attempt=len(task.attempts) + 1,
            baseline_sha=baseline_sha,
            specialist_id=specialist_id or task.specialist_id,
            started_at=_utc_now_iso(),
            remediation=self._safe(remediation, MAX_LEDGER_OUTPUT_CHARS),
            workspace_dir=workspace_dir,
            branch=branch,
        )
        updated = replace(
            task,
            state=TASK_RUNNING,
            baseline_sha=baseline_sha,
            attempts=task.attempts + (attempt,),
        )
        return self._write(replace(record, tasks=self._replace_task(record, updated)))

    def record_attempt_interrupted(self, mission_id: str, task_id: str) -> MissionRecord:
        """Close an attempt whose process died before a trusted result existed.

        An interrupted attempt is terminal and never accepted: a candidate
        commit alone proves nothing without validation, QA and the Reviewer.
        It consumes its slot in the bound, so a crash loop cannot mint attempts.
        """
        record = self.load(mission_id)
        self._require_running(record)
        task = record.task(task_id)
        attempt = task.open_attempt
        if attempt is None:
            raise MissionLedgerError(f"task {task_id} has no attempt in flight")
        closed = replace(
            attempt,
            status=ATTEMPT_INTERRUPTED,
            ended_at=_utc_now_iso(),
            failure_detail="attempt interrupted before a trusted terminal result",
        )
        updated = replace(
            task,
            state=ATTEMPT_INTERRUPTED,
            failure_detail=closed.failure_detail,
            attempts=self._replace_attempt(task, closed),
        )
        return self._write(replace(record, tasks=self._replace_task(record, updated)))

    @staticmethod
    def _replace_attempt(
        task: MissionTaskRecord, updated: MissionAttemptRecord
    ) -> tuple[MissionAttemptRecord, ...]:
        return tuple(
            updated if a.attempt == updated.attempt else a for a in task.attempts
        )

    def record_task_result(
        self, mission_id: str, result: TaskExecutionResult
    ) -> MissionRecord:
        """Record a REAL task execution result and derive the task state from it.

        ``accepted`` is not taken on trust: the whole T4 chain - a candidate
        commit, real validation results that all passed, a passing QA verdict
        and a passing Reviewer verdict - must be present in the result object,
        or the ledger refuses to store an accepted task.
        """
        record = self.load(mission_id)
        self._require_running(record)
        task = record.task(result.task_id)
        if task.state != TASK_RUNNING:
            raise MissionLedgerError(f"task {result.task_id} is {task.state}, not running")
        attempt = task.open_attempt
        if attempt is None:
            raise MissionLedgerError(f"task {result.task_id} has no attempt in flight")
        # Collapse the reported status to a plain string first: a str
        # subclass with a rigged __eq__, or any object at all, must not be
        # able to match one allowlist entry while carrying another value.
        status = str(result.status)
        if status == STATUS_ACCEPTED:
            if not self._chain_complete(result):
                raise MissionLedgerError(
                    f"task {result.task_id} claims accepted without the full "
                    "implementation, validation, QA and Reviewer chain"
                )
            state = TASK_ACCEPTED
        elif status in TASK_FAILURE_STATES:
            state = status
        else:
            raise MissionLedgerError(
                f"task {result.task_id} reported an unknown status: {status!r}"
            )
        closed = replace(
            attempt,
            status=status,
            ended_at=_utc_now_iso(),
            candidate_sha=result.candidate_sha or "",
            changed_files=result.changed_files,
            implementation_summary=self._safe(result.implementation_summary),
            validation=_bounded_validation(result.validation_results, self._safe),
            qa=_bounded_verdict(result.qa, self._safe),
            reviewer=_bounded_verdict(result.reviewer, self._safe),
            failure_detail=self._safe(result.failure_detail),
        )
        # The task-level fields project the LATEST attempt; the attempt list
        # keeps every earlier one exactly as it happened.
        updated = replace(
            task,
            state=state,
            candidate_sha=closed.candidate_sha,
            changed_files=closed.changed_files,
            implementation_summary=closed.implementation_summary,
            validation=closed.validation,
            qa=closed.qa,
            reviewer=closed.reviewer,
            failure_detail=closed.failure_detail,
            attempts=self._replace_attempt(task, closed),
        )
        return self._write(replace(record, tasks=self._replace_task(record, updated)))

    @staticmethod
    def _chain_complete(result: TaskExecutionResult) -> bool:
        return (
            bool(result.candidate_sha)
            and bool(result.validation_results)
            and all(item.passed for item in result.validation_results)
            and result.qa is not None
            and result.qa.passed
            and result.reviewer is not None
            and result.reviewer.passed
        )

    @staticmethod
    def _replace_task(
        record: MissionRecord, updated: MissionTaskRecord
    ) -> tuple[MissionTaskRecord, ...]:
        return tuple(
            updated if task.task_id == updated.task_id else task for task in record.tasks
        )

    def record_frontier_advanced(
        self, mission_id: str, task_id: str, *, frontier_sha: str
    ) -> MissionRecord:
        """Advance the frontier after an accepted candidate was integrated."""
        record = self.load(mission_id)
        self._require_running(record)
        task = record.task(task_id)
        if task.state != TASK_ACCEPTED:
            raise MissionLedgerError(
                f"task {task_id} is {task.state}; only an accepted candidate integrates"
            )
        if not frontier_sha:
            raise MissionLedgerError("an advanced frontier needs a commit SHA")
        updated = replace(task, state=TASK_INTEGRATED, integrated_sha=frontier_sha)
        return self._write(
            replace(
                record, tasks=self._replace_task(record, updated), frontier_sha=frontier_sha
            )
        )

    def record_final_validation(
        self, mission_id: str, results: tuple[ValidationResult, ...], *, frontier_sha: str
    ) -> MissionRecord:
        """Record the fresh final validation of the complete frontier.

        Refuses to run before every planned task is integrated, so the stored
        evidence always describes the finished deliverable. A failing run puts
        the mission into ``final_validation_failed``; it can never reach the
        approval gate.
        """
        record = self.load(mission_id)
        self._require_running(record)
        if not record.all_tasks_integrated:
            raise MissionLedgerError(
                f"mission {mission_id} has tasks that are not integrated; "
                "final validation describes the complete frontier only"
            )
        if not results:
            raise MissionLedgerError("final validation must execute at least one command")
        # The evidence is bound to the tree it actually described. If the
        # deliverable moved under the mission, the green run belongs to a
        # different tree than the one the gate would approve.
        if frontier_sha != record.frontier_sha:
            raise MissionLedgerError(
                f"final validation ran against {frontier_sha}, not the mission "
                f"frontier {record.frontier_sha}"
            )
        evidence = _bounded_validation(results, self._safe)
        passed = all(item.passed for item in results)
        blocker = (
            None
            if passed
            else {
                "reason": MISSION_FINAL_VALIDATION_FAILED,
                "detail": "fresh validation of the final frontier failed",
                "task_id": "",
            }
        )
        return self._write(
            replace(
                record,
                final_validation=evidence,
                final_validation_sha=frontier_sha,
                status=MISSION_RUNNING if passed else MISSION_FINAL_VALIDATION_FAILED,
                blocker=blocker,
            )
        )

    def record_awaiting_approval(self, mission_id: str) -> MissionRecord:
        """Enter the human approval gate - only when everything really passed.

        The invariant is recomputed from stored state, never asserted by the
        caller: every planned task integrated, and a recorded fresh final
        validation in which every command passed.
        """
        record = self.load(mission_id)
        self._require_running(record)
        if not record.all_tasks_integrated:
            raise MissionLedgerError(
                f"mission {mission_id} cannot await approval with unintegrated tasks"
            )
        if record.final_validation is None:
            raise MissionLedgerError(
                f"mission {mission_id} cannot await approval without final validation"
            )
        if not record.final_validation_passed:
            raise MissionLedgerError(
                f"mission {mission_id} cannot await approval with failed final validation"
            )
        if record.final_validation_sha != record.frontier_sha:
            raise MissionLedgerError(
                f"mission {mission_id} validated {record.final_validation_sha} but its "
                f"frontier is {record.frontier_sha}; refusing to approve an unvalidated tree"
            )
        return self._write(
            replace(
                record,
                status=MISSION_AWAITING_HUMAN_APPROVAL,
                final_frontier_sha=record.frontier_sha,
            )
        )

    def record_blocked(
        self, mission_id: str, *, reason: str, detail: str, task_id: str = ""
    ) -> MissionRecord:
        """Stop the mission cleanly and persist exactly why. T5 never retries."""
        record = self.load(mission_id)
        self._require_running(record)
        return self._write(
            replace(
                record,
                status=MISSION_BLOCKED,
                blocker={
                    "reason": self._safe(reason),
                    "detail": self._safe(detail),
                    "task_id": task_id,
                },
            )
        )

    @contextmanager
    def execution_lock(self, mission_id: str) -> Iterator[None]:
        """Hold the exclusive right to EXECUTE this mission.

        Task execution is a long load-check-write sequence - open an attempt,
        run it, record it, integrate - and durable writes are last-write-wins.
        Two engines on one mission therefore lose attempt records, spend each
        other's recovery budget, and can close a live attempt as interrupted.
        A kernel lock makes a second engine fail closed instead, and releases
        automatically if the holder dies, so a crash never wedges the mission.
        """
        self._path(mission_id)  # validates the id before it becomes a path
        path = self.missions_dir / f"{mission_id}.execution.lock"
        handle = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o644)
        try:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise MissionLedgerError(
                    f"mission {mission_id} is already being executed by another process"
                ) from exc
            yield
        finally:
            os.close(handle)

    def _lock_path(self, mission_id: str) -> Path:
        # Deliberately not a *.json name: mission listing must not see it.
        self._path(mission_id)  # validates the id before it becomes a path
        return self.missions_dir / f"{mission_id}.decision.lock"

    @contextmanager
    def decision_lock(self, mission_id: str) -> Iterator[MissionRecord]:
        """Hold the exclusive right to decide this mission, and yield its record.

        The whole decision - writing the approval record and moving the
        mission out of the gate - is a load, check, write sequence, and
        durable writes are last-write-wins. Without mutual exclusion two
        concurrent operators both pass the gate and one silently overwrites
        the other's decision.

        This is a kernel lock rather than a marker file on purpose: if the
        deciding process dies at any point - Ctrl-C, SIGKILL, a lost host -
        the kernel drops the lock, so an interrupted decision can never wedge
        a finished mission into a state nobody can approve or reject. The
        status is re-read INSIDE the lock, so the loser sees the winner's
        decision rather than racing it.
        """
        path = self._lock_path(mission_id)
        handle = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o644)
        try:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise MissionLedgerError(
                    f"mission {mission_id} already has a decision in progress"
                ) from exc
            record = self.load(mission_id)
            if record.status != MISSION_AWAITING_HUMAN_APPROVAL:
                raise MissionLedgerError(
                    f"mission {mission_id} is {record.status}; only a mission awaiting "
                    "approval can be approved or rejected"
                )
            yield record
        finally:
            os.close(handle)  # releasing the descriptor releases the lock

    def record_approval_decision(
        self,
        mission_id: str,
        *,
        operator: str,
        decision: bool,
        digest: str,
        action: str,
        timestamp: str | None = None,
    ) -> MissionRecord:
        """Record an explicit human decision on a mission awaiting approval.

        Fails closed for any mission not currently at the gate, which also
        makes a second decision on an already-decided mission an error rather
        than a silent overwrite.
        """
        record = self.load(mission_id)
        if record.status != MISSION_AWAITING_HUMAN_APPROVAL:
            raise MissionLedgerError(
                f"mission {mission_id} is {record.status}; only a mission awaiting "
                "approval can be approved or rejected"
            )
        return self._write(
            replace(
                record,
                status=MISSION_APPROVED if decision else MISSION_REJECTED,
                approval={
                    "operator": operator,
                    "decision": bool(decision),
                    "digest": digest,
                    "action": action,
                    "timestamp": timestamp or _utc_now_iso(),
                    "frontier_sha": record.final_frontier_sha,
                },
            )
        )


def mission_approval_action(mission_id: str) -> str:
    """The approval action name for one mission run."""
    return f"{MISSION_ACTION_PREFIX}{mission_id}"


def approve_mission(
    store: MissionStore,
    port: Any,
    mission_id: str,
    *,
    operator: str,
    decision: bool = True,
    contract_revision: str | None = None,
    roadmap_revision: str | None = None,
) -> MissionRecord:
    """Record an explicit human decision on a completed mission.

    Reuses the existing durable ApprovalPort, binding the approval to the
    exact final frontier SHA that was validated and reviewed. Approval is a
    DECISION only: this writes durable records and does nothing else - no
    push, no remote configuration, no deployment, no publishing. The
    deliverable stays local and inspectable.

    Fails closed for any mission that is not currently at the approval gate:
    a blocked mission, a mission whose final validation failed, one still
    running, and an already-decided mission are all refused.
    """
    if not store.exists(mission_id):
        raise MissionLedgerError(f"unknown mission: {mission_id}")
    with store.decision_lock(mission_id) as record:
        if not record.final_frontier_sha:
            raise MissionLedgerError(
                f"mission {mission_id} has no final frontier to approve"
            )
        action = mission_approval_action(mission_id)
        digest = port.approve(
            action,
            record.final_frontier_sha,
            operator,
            decision=bool(decision),
            contract_revision=contract_revision,
            roadmap_revision=roadmap_revision,
        )
        return store.record_approval_decision(
            mission_id,
            operator=operator,
            decision=bool(decision),
            digest=str(digest),
            action=action,
        )


def attempts_remaining(record: MissionRecord, task_id: str) -> int:
    """Attempts still available to a task, derived purely from durable history."""
    return max(0, record.max_attempts_per_task - len(record.task(task_id).attempts))


def rebuild_task_contracts(record: MissionRecord) -> tuple[TaskContract, ...]:
    """Rebuild the pinned plan from durable state.

    Resume must never ask the planner again: the objective, the DAG, the task
    identities, their dependencies and their required capabilities are fixed
    when the mission is created and are replayed verbatim here.
    """
    contracts: list[TaskContract] = []
    for task in record.tasks:
        try:
            contracts.append(
                TaskContract(
                    id=task.task_id,
                    objective_id=record.objective_id,
                    instruction=task.instruction,
                    acceptance_criteria=task.acceptance_criteria,
                    dependencies=task.dependencies,
                    required_capabilities=task.required_capabilities,
                )
            )
        except ValueError as exc:
            raise MissionLedgerError(
                f"mission {record.mission_id} task {task.task_id} cannot be rebuilt: {exc}"
            ) from exc
    return tuple(contracts)


def remediation_context(attempt: MissionAttemptRecord) -> str:
    """Bounded correction context describing why the previous attempt failed.

    Correction context only: it reports what went wrong, and carries no
    authority to change the objective, the task, its dependencies, its
    required capabilities, or any safety policy. Everything here already
    passed through the ledger's redaction and bounds.
    """
    lines: list[str] = [
        f"A previous attempt (#{attempt.attempt}) at this exact task ended as "
        f"{attempt.status}. Fix the specific problem below; the task, its "
        f"acceptance criteria and its scope are unchanged."
    ]
    if attempt.status == STATUS_VALIDATION_FAILED:
        for item in attempt.validation:
            if item.get("passed"):
                continue
            label = item.get("label")
            tail = str(item.get("stderr") or item.get("stdout") or "")[-800:]
            lines.append(f"failing validation command [{label}] exit={item.get('exit_code')}")
            if tail.strip():
                lines.append(f"output tail: {tail}")
    elif attempt.status == STATUS_QA_BLOCKED and attempt.qa is not None:
        lines.append(f"QA blocked: {attempt.qa.get('summary', '')}")
        for blocker in attempt.qa.get("blockers", []):
            lines.append(f"QA blocker: {blocker}")
    elif attempt.status == STATUS_REVIEWER_BLOCKED and attempt.reviewer is not None:
        lines.append(f"Reviewer blocked: {attempt.reviewer.get('summary', '')}")
        for blocker in attempt.reviewer.get("blockers", []):
            lines.append(f"Reviewer blocker: {blocker}")
    elif attempt.status == STATUS_EXECUTION_FAILED and attempt.failure_detail:
        lines.append(f"execution failure: {attempt.failure_detail}")
    return _clip("\n".join(lines), MAX_REMEDIATION_CHARS)
