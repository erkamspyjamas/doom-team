"""Happy-path mission orchestration: objective in, approval gate out.

One canonical runtime owns the whole sequence, so the product path and the
tests exercise the same engine:

    objective -> Planner -> validated Mission/DAG -> automatic routing
    -> serial task execution through the production TaskRunner
    -> real validation, independent QA, independent Reviewer
    -> accepted candidate integrated into the deliverable frontier
    -> ... next task from the ADVANCED frontier ...
    -> fresh final validation of the finished frontier
    -> AWAITING_HUMAN_APPROVAL

Every step reports to the durable :class:`~doom_team.mission.MissionStore`,
which derives the recorded state from the real runtime objects rather than
from anything this module asserts.

Ordering is serial-first: a task runs only once all of its dependencies are
accepted AND integrated, and the ready set is taken in deterministic task-id
order. Nobody supplies an execution sequence or an agent role - the DAG fixes
the order and capability routing fixes the specialist.

There is no recovery here by design. A task that does not reach ``accepted``
stops the mission cleanly with its blocker persisted and its workspace left
on disk for inspection; dependent tasks never run and no failed candidate is
ever integrated. Retry, remediation and resume belong to a later layer.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from doom_team.deliverable import DeliverableError, DeliverableRepo
from doom_team.domain import Mission, TaskContract
from doom_team.mission import (
    MISSION_AWAITING_HUMAN_APPROVAL,
    MissionLedgerError,
    MissionRecord,
    MissionStore,
    new_mission_id,
)
from doom_team.observability import Event, EventSink
from doom_team.planner import Planner, PlanningError
from doom_team.ports import InferencePort
from doom_team.routing import RoutingDecision, RoutingError, route_task, specialist_by_id
from doom_team.task_runner import STATUS_ACCEPTED, ProductionTaskRunner, TaskRunnerError
from doom_team.validation import (
    ValidationCommand,
    ValidationExecutionError,
    run_validation_commands,
)

FINAL_VALIDATION_TASK_ID = "final-validation"

# Trusted deterministic validation for a generated Python deliverable. These
# are OUR commands, never anything the model authored.
DEFAULT_VALIDATION_COMMANDS: tuple[ValidationCommand, ...] = (
    ValidationCommand(label="compile", argv=(sys.executable, "-m", "compileall", "-q", ".")),
    ValidationCommand(label="tests", argv=(sys.executable, "-m", "pytest", "-q")),
)


class MissionRunError(Exception):
    """Raised when a mission cannot even be started (fail closed)."""


@dataclass(frozen=True)
class MissionRunResult:
    """Immutable outcome of one mission run.

    Deliberately distinct from a task result: ``status`` is a mission status,
    so an accepted task can never be mistaken for a mission awaiting approval,
    and a mission awaiting approval can never be mistaken for an approved one.
    Approval is a separate human decision recorded later.
    """

    mission_id: str
    status: str
    objective: str
    deliverable_path: str
    frontier_sha: str
    record: MissionRecord

    @property
    def awaiting_approval(self) -> bool:
        return self.status == MISSION_AWAITING_HUMAN_APPROVAL

    @property
    def approval_required(self) -> bool:
        return self.record.approval_required

    def as_dict(self) -> dict[str, Any]:
        """Structured, secret-free summary for operators and the CLI."""
        record = self.record
        return {
            "mission_id": self.mission_id,
            "status": self.status,
            "objective": self.objective,
            "deliverable_path": self.deliverable_path,
            "baseline_sha": record.baseline_sha,
            "frontier_sha": self.frontier_sha,
            "final_frontier_sha": record.final_frontier_sha,
            "approval_required": self.approval_required,
            "blocker": record.blocker,
            "tasks": [
                {
                    "task_id": task.task_id,
                    "state": task.state,
                    "specialist_id": task.specialist_id,
                    "dependencies": list(task.dependencies),
                    "baseline_sha": task.baseline_sha,
                    "candidate_sha": task.candidate_sha,
                    "integrated_sha": task.integrated_sha,
                    "changed_files": list(task.changed_files),
                    "validation": [
                        {"label": item.get("label"), "passed": item.get("passed")}
                        for item in task.validation
                    ],
                    "qa": None if task.qa is None else task.qa.get("verdict"),
                    "reviewer": None if task.reviewer is None else task.reviewer.get("verdict"),
                    "failure_detail": task.failure_detail,
                }
                for task in record.tasks
            ],
            "routing": [dict(item) for item in record.routing],
            "final_validation": (
                None
                if record.final_validation is None
                else [
                    {"label": item.get("label"), "passed": item.get("passed")}
                    for item in record.final_validation
                ]
            ),
        }


class MissionRunner:
    """The canonical happy-path mission engine."""

    def __init__(
        self,
        inference: InferencePort,
        store: MissionStore,
        *,
        workspaces_root: str | Path,
        validation_commands: tuple[ValidationCommand, ...] = DEFAULT_VALIDATION_COMMANDS,
        final_validation_commands: tuple[ValidationCommand, ...] | None = None,
        event_sink: EventSink | None = None,
    ) -> None:
        if not validation_commands:
            raise MissionRunError("a mission requires at least one validation command")
        self._inference = inference
        self._store = store
        self._workspaces_root = Path(workspaces_root)
        self._validation_commands = tuple(validation_commands)
        self._final_validation_commands = tuple(
            final_validation_commands
            if final_validation_commands is not None
            else validation_commands
        )
        self._event_sink = event_sink
        self._task_runner = ProductionTaskRunner(
            inference, validation_commands=self._validation_commands
        )

    def _emit(self, kind: str, details: dict[str, Any]) -> None:
        if self._event_sink is not None:
            self._event_sink.record(
                Event(kind=kind, details=details, producer_role="mission")
            )

    def run(self, objective: str, destination: str | Path) -> MissionRunResult:
        """Execute one complete happy-path mission. Never pushes or deploys."""
        mission = self._plan(objective)
        mission_id = new_mission_id(mission.id)
        self._store.create(
            mission_id=mission_id,
            objective=objective,
            plan_mission_id=mission.id,
            objective_id=mission.objective.id,
        )
        self._emit("MISSION_STARTED", {"mission_id": mission_id, "tasks": len(mission.tasks)})
        self._store.record_plan(mission_id, mission)

        try:
            decisions = self._route(mission)
        except RoutingError as exc:
            return self._blocked(mission_id, "routing_failed", str(exc))
        self._store.record_routing(mission_id, tuple(decisions[t.id] for t in mission.tasks))

        try:
            repo = self._open_deliverable(destination)
        except DeliverableError as exc:
            return self._blocked(mission_id, "deliverable_unavailable", str(exc))
        self._store.record_deliverable(
            mission_id, deliverable_path=str(repo.path), baseline_sha=repo.head()
        )

        return self._execute(mission_id, mission, decisions, repo)

    def _plan(self, objective: str) -> Mission:
        try:
            return Planner(self._inference).plan(objective)
        except PlanningError as exc:
            # Nothing durable exists yet: there is no mission to block.
            raise MissionRunError(f"planning failed: {exc}") from exc

    @staticmethod
    def _route(mission: Mission) -> dict[str, RoutingDecision]:
        """Automatic capability routing for every task. No operator input."""
        return {task.id: route_task(task) for task in mission.tasks}

    @staticmethod
    def _open_deliverable(destination: str | Path) -> DeliverableRepo:
        path = Path(destination).expanduser()
        if (path / ".git").is_dir():
            return DeliverableRepo.open(path)
        return DeliverableRepo.create(path)

    def _blocked(self, mission_id: str, reason: str, detail: str, task_id: str = "") -> MissionRunResult:
        try:
            record = self._store.record_blocked(
                mission_id, reason=reason, detail=detail, task_id=task_id
            )
        except MissionLedgerError:
            # Already terminal: report the state that is actually stored
            # rather than overwriting a recorded outcome.
            record = self._store.load(mission_id)
        self._emit(
            "MISSION_BLOCKED",
            {"mission_id": mission_id, "reason": reason, "task_id": task_id},
        )
        return self._result(record)

    @staticmethod
    def _result(record: MissionRecord) -> MissionRunResult:
        return MissionRunResult(
            mission_id=record.mission_id,
            status=record.status,
            objective=record.objective,
            deliverable_path=record.deliverable_path,
            frontier_sha=record.frontier_sha,
            record=record,
        )

    def _execute(
        self,
        mission_id: str,
        mission: Mission,
        decisions: dict[str, RoutingDecision],
        repo: DeliverableRepo,
    ) -> MissionRunResult:
        by_id = {task.id: task for task in mission.tasks}
        accepted: set[str] = set()
        remaining = [task.id for task in mission.tasks]

        while remaining:
            ready = self._ready(remaining, by_id, accepted)
            if ready is None:
                return self._blocked(
                    mission_id,
                    "no_ready_task",
                    f"remaining tasks {sorted(remaining)} have unsatisfied dependencies",
                )
            task = by_id[ready]
            try:
                outcome = self._run_task(mission_id, task, decisions[ready], repo, accepted)
            except MissionLedgerError as exc:
                # The ledger refused a transition - a frontier that moved under
                # the mission, for example. Stop cleanly instead of crashing.
                return self._blocked(mission_id, "ledger_refused", str(exc), ready)
            if outcome is not None:
                return outcome
            accepted.add(ready)
            remaining.remove(ready)

        return self._finalize(mission_id, repo)

    @staticmethod
    def _ready(
        remaining: list[str], by_id: dict[str, TaskContract], accepted: set[str]
    ) -> str | None:
        """First runnable task in deterministic id order, or None if stuck."""
        for task_id in sorted(remaining):
            if set(by_id[task_id].dependencies) <= accepted:
                return task_id
        return None

    def _run_task(
        self,
        mission_id: str,
        task: TaskContract,
        decision: RoutingDecision,
        repo: DeliverableRepo,
        accepted: set[str],
    ) -> MissionRunResult | None:
        """Run one task; returns a terminal result only when the mission stops."""
        frontier = repo.head()
        try:
            specialist = specialist_by_id(decision.specialist_id)
        except RoutingError as exc:
            return self._blocked(mission_id, "routing_failed", str(exc), task.id)
        try:
            # The workspace baseline IS the current frontier, so every task
            # builds on the integrated work of its dependencies.
            workspace = repo.create_task_workspace(
                task.id, self._workspaces_root, baseline_sha=frontier
            )
        except DeliverableError as exc:
            return self._blocked(mission_id, "workspace_failed", str(exc), task.id)

        self._store.record_task_started(mission_id, task.id, baseline_sha=frontier)
        self._emit(
            "TASK_STARTED",
            {
                "mission_id": mission_id,
                "task_id": task.id,
                "specialist_id": decision.specialist_id,
                "baseline_sha": frontier,
            },
        )
        try:
            result = self._task_runner.run_task(
                task,
                decision,
                specialist,
                workspace,
                objective_summary=self._objective_summary(mission_id),
                dependency_summary=self._dependency_summary(accepted),
            )
        except TaskRunnerError as exc:
            return self._blocked(mission_id, "task_runner_error", str(exc), task.id)

        self._store.record_task_result(mission_id, result)
        self._emit(
            "TASK_FINISHED",
            {"mission_id": mission_id, "task_id": task.id, "status": result.status},
        )
        if result.status != STATUS_ACCEPTED:
            # No retry, no remediation: the candidate stays out of the
            # deliverable and the workspace is preserved for inspection.
            return self._blocked(
                mission_id,
                result.status,
                result.failure_detail or f"task {task.id} did not reach accepted",
                task.id,
            )

        try:
            frontier_sha = repo.integrate_candidate(workspace)
        except DeliverableError as exc:
            return self._blocked(mission_id, "integration_failed", str(exc), task.id)
        self._store.record_frontier_advanced(mission_id, task.id, frontier_sha=frontier_sha)
        self._emit(
            "FRONTIER_ADVANCED",
            {"mission_id": mission_id, "task_id": task.id, "frontier_sha": frontier_sha},
        )
        return None

    def _objective_summary(self, mission_id: str) -> str:
        return self._store.load(mission_id).objective

    @staticmethod
    def _dependency_summary(accepted: set[str]) -> str:
        if not accepted:
            return ""
        return "already integrated tasks: " + ", ".join(sorted(accepted))

    def _finalize(self, mission_id: str, repo: DeliverableRepo) -> MissionRunResult:
        """Fresh validation of the finished frontier, then the approval gate.

        The validation runs in a throwaway clone of the final frontier rather
        than in the deliverable itself, so executing the generated project can
        never mutate the artifact under review.
        """
        frontier = repo.head()
        # The tree that gets validated must be the tree the gate approves. If
        # the deliverable moved since the last integration, the mission stops
        # rather than certify a frontier it never built.
        ledger_frontier = self._store.load(mission_id).frontier_sha
        if frontier != ledger_frontier:
            return self._blocked(
                mission_id,
                "frontier_moved",
                f"deliverable head {frontier} is not the mission frontier {ledger_frontier}",
            )
        try:
            workspace = repo.create_task_workspace(
                FINAL_VALIDATION_TASK_ID, self._workspaces_root, baseline_sha=frontier
            )
            results = run_validation_commands(workspace.path, self._final_validation_commands)
        except (DeliverableError, ValidationExecutionError) as exc:
            return self._blocked(mission_id, "final_validation_unavailable", str(exc))

        try:
            record = self._store.record_final_validation(
                mission_id, results, frontier_sha=frontier
            )
        except MissionLedgerError as exc:
            return self._blocked(mission_id, "ledger_refused", str(exc))
        self._emit(
            "FINAL_VALIDATION",
            {
                "mission_id": mission_id,
                "frontier_sha": frontier,
                "passed": record.final_validation_passed,
            },
        )
        if not record.final_validation_passed:
            return self._result(record)

        try:
            record = self._store.record_awaiting_approval(mission_id)
        except MissionLedgerError as exc:
            return self._blocked(mission_id, "approval_gate_refused", str(exc))
        self._emit(
            "MISSION_AWAITING_APPROVAL",
            {"mission_id": mission_id, "frontier_sha": record.final_frontier_sha},
        )
        return self._result(record)
