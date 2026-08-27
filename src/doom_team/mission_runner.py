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

Failure is bounded, not open-ended. A task that does not reach ``accepted``
may be attempted again - from the CURRENT frontier, in a fresh workspace,
carrying a narrow correction context describing what went wrong - until its
durable attempt budget is spent, after which the mission stops cleanly with
every attempt preserved. The budget is counted from persisted history, so
restarting the process never refills it, and every terminal attempt occupies
a slot, including provider failures and attempts lost to a crash.

Interruption is survivable. ``resume`` rebuilds the plan, the routing and the
frontier from the ledger - never from the planner and never from memory -
closes any attempt that died mid-flight as ``interrupted`` rather than
accepted, and reconciles the ledger against Git truth so a candidate that was
fast-forwarded before the ledger write is recorded rather than applied twice.
Where provenance is ambiguous the mission stops instead of guessing. One
engine executes a mission at a time, enforced by a kernel lock.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from doom_team.deliverable import DeliverableError, DeliverableRepo, TaskWorkspace
from doom_team.domain import Mission, TaskContract
from doom_team.durable import StateCorruptionError
from doom_team.mission import (
    DEFAULT_SEMANTIC_RETRIES,
    MISSION_AWAITING_HUMAN_APPROVAL,
    MISSION_RUNNING,
    TASK_ACCEPTED,
    MissionLedgerError,
    MissionRecord,
    MissionStore,
    attempts_remaining,
    max_attempts_for,
    new_mission_id,
    rebuild_task_contracts,
    remediation_context,
)
from doom_team.observability import Event, EventSink
from doom_team.planner import Planner, PlanningError
from doom_team.ports import InferencePort
from doom_team.routing import RoutingDecision, RoutingError, route_task, specialist_by_id
from doom_team.task_runner import STATUS_ACCEPTED, ProductionTaskRunner, TaskRunnerError
from doom_team.transport import (
    InProcessTaskTransport,
    TaskAttemptRequest,
    TaskExecutionTransport,
    TransportError,
    transport_failure,
)
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
        semantic_retries: int = DEFAULT_SEMANTIC_RETRIES,
        transport: TaskExecutionTransport | None = None,
        validation_policy: str = "default",
    ) -> None:
        if not validation_commands:
            raise MissionRunError("a mission requires at least one validation command")
        try:
            self._max_attempts = max_attempts_for(semantic_retries)
        except MissionLedgerError as exc:
            raise MissionRunError(str(exc)) from exc
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
        # Where a task attempt executes is a transport concern; everything the
        # mission decides is unchanged either way. The default keeps the
        # Gate-A behaviour exactly, with no external dependency.
        self._transport: TaskExecutionTransport = (
            transport if transport is not None else InProcessTaskTransport(self._task_runner)
        )
        self._validation_policy = validation_policy

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
            max_attempts_per_task=self._max_attempts,
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

        with self._store.execution_lock(mission_id):
            return self._execute(mission_id, mission.tasks, decisions, repo)

    def resume(self, mission_id: str) -> MissionRunResult:
        """Re-enter a mission from durable state after a process interruption.

        Explicit operator action, never a background daemon. Nothing is taken
        from memory: the plan, the DAG, the routing inputs, the frontier and
        the remaining recovery budget are all replayed from the ledger, and
        the deliverable repository is reopened from the recorded path.

        A mission that is already finished with this layer - awaiting
        approval, approved, rejected, blocked, or failed final validation -
        is reported, never re-executed.
        """
        try:
            if not self._store.exists(mission_id):
                raise MissionRunError(f"unknown mission: {mission_id}")
            record = self._store.load(mission_id)
        except MissionLedgerError as exc:
            raise MissionRunError(str(exc)) from exc
        except StateCorruptionError:
            # Distinct from a run failure: the CLI maps this to a state error.
            raise
        if record.status != MISSION_RUNNING:
            # Terminal for this layer: report the durable truth untouched.
            self._emit(
                "MISSION_RESUME_NOOP",
                {"mission_id": mission_id, "status": record.status},
            )
            return self._result(record)

        try:
            repo = DeliverableRepo.open(record.deliverable_path)
        except DeliverableError as exc:
            return self._blocked(mission_id, "deliverable_unavailable", str(exc))

        with self._store.execution_lock(mission_id):
            return self._resume_locked(mission_id, repo)

    def _resume_locked(self, mission_id: str, repo: DeliverableRepo) -> MissionRunResult:
        """Reconcile and continue, holding the mission's execution lock."""
        # Re-read inside the lock: another engine may have finished the
        # mission between the status check and acquiring exclusivity.
        record = self._store.load(mission_id)
        if record.status != MISSION_RUNNING:
            return self._result(record)

        outcome = self._reconcile(mission_id, repo)
        if outcome is not None:
            return outcome

        record = self._store.load(mission_id)
        try:
            tasks = rebuild_task_contracts(record)
        except MissionLedgerError as exc:
            return self._blocked(mission_id, "plan_unreadable", str(exc))
        try:
            decisions = {task.id: route_task(task) for task in tasks}
        except RoutingError as exc:
            return self._blocked(mission_id, "routing_failed", str(exc))

        self._emit(
            "MISSION_RESUMED",
            {"mission_id": mission_id, "frontier_sha": record.frontier_sha},
        )
        if all(t.integrated for t in record.tasks):
            # Every task landed; only the final gate is left to redo.
            return self._finalize(mission_id, repo)
        return self._execute(mission_id, tasks, decisions, repo)

    def _reconcile(self, mission_id: str, repo: DeliverableRepo) -> MissionRunResult | None:
        """Reconcile durable state with Git truth before resuming execution.

        Three crash windows matter here. An attempt still marked in flight had
        no trusted terminal result, so it is closed as interrupted - never as
        accepted, whatever files or commits it left behind. An accepted
        candidate that Git shows is already an ancestor of the deliverable was
        integrated before the ledger write, so the ledger is caught up instead
        of the commit being applied twice. Anything else that moved the
        deliverable is unexplained, and unexplained provenance stops the
        mission rather than guessing.
        """
        record = self._store.load(mission_id)
        for task in record.tasks:
            if task.open_attempt is not None:
                self._store.record_attempt_interrupted(mission_id, task.task_id)
                self._emit(
                    "ATTEMPT_INTERRUPTED",
                    {"mission_id": mission_id, "task_id": task.task_id},
                )

        record = self._store.load(mission_id)
        for task in record.tasks:
            if task.state != TASK_ACCEPTED or not task.candidate_sha:
                continue
            try:
                already_in = repo.contains(task.candidate_sha)
            except DeliverableError as exc:
                return self._blocked(mission_id, "reconcile_failed", str(exc), task.task_id)
            if already_in:
                # Advance to the CANDIDATE, not to today's head. Anything
                # stacked on top while the mission was down is unreviewed, and
                # adopting it here would smuggle it past QA and the Reviewer;
                # the head check below turns that into a fail-closed block.
                self._store.record_frontier_advanced(
                    mission_id, task.task_id, frontier_sha=task.candidate_sha
                )
                self._emit(
                    "INTEGRATION_RECONCILED",
                    {"mission_id": mission_id, "task_id": task.task_id},
                )

        record = self._store.load(mission_id)
        try:
            head = repo.head()
        except DeliverableError as exc:
            return self._blocked(mission_id, "deliverable_unavailable", str(exc))
        if head != record.frontier_sha:
            return self._blocked(
                mission_id,
                "frontier_ambiguous",
                f"deliverable head {head} does not match the mission frontier "
                f"{record.frontier_sha} and is not explained by any recorded candidate",
            )
        return None

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
        tasks: tuple[TaskContract, ...],
        decisions: dict[str, RoutingDecision],
        repo: DeliverableRepo,
    ) -> MissionRunResult:
        by_id = {task.id: task for task in tasks}
        record = self._store.load(mission_id)
        accepted = {t.task_id for t in record.tasks if t.integrated}
        remaining = [task.id for task in tasks if task.id not in accepted]

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
        """Drive one task to acceptance within its bounded attempt budget.

        Returns None when the task is integrated and the mission may continue,
        or a terminal mission result when it cannot. Every attempt starts from
        the CURRENT frontier in a fresh workspace: a failed attempt's dirty
        workspace is preserved for inspection but never becomes the base of
        the next one.
        """
        while True:
            record = self._store.load(mission_id)
            task_record = record.task(task.id)

            if task_record.integrated:
                return None
            # Crashed after acceptance but before integration: the work is
            # already trusted, so finish the integration rather than re-run it.
            if task_record.state == TASK_ACCEPTED:
                outcome = self._integrate(mission_id, task, repo, task_record)
                if outcome is not None:
                    return outcome
                return None

            if attempts_remaining(record, task.id) <= 0:
                last = task_record.attempts[-1] if task_record.attempts else None
                return self._blocked(
                    mission_id,
                    "recovery_exhausted",
                    (
                        f"task {task.id} used all {record.max_attempts_per_task} "
                        f"attempts; last outcome {task_record.state}"
                        + (f": {last.failure_detail}" if last and last.failure_detail else "")
                    ),
                    task.id,
                )

            outcome = self._one_attempt(mission_id, task, decision, repo, accepted)
            if outcome is not None:
                return outcome
            # Loop: either the task is now accepted/integrated, or a fresh
            # bounded attempt follows if any budget remains.

    def _one_attempt(
        self,
        mission_id: str,
        task: TaskContract,
        decision: RoutingDecision,
        repo: DeliverableRepo,
        accepted: set[str],
    ) -> MissionRunResult | None:
        """Execute exactly one attempt. Returns a result only to stop the mission."""
        record = self._store.load(mission_id)
        task_record = record.task(task.id)
        frontier = repo.head()
        if frontier != record.frontier_sha:
            return self._blocked(
                mission_id,
                "frontier_moved",
                f"deliverable head {frontier} is not the mission frontier "
                f"{record.frontier_sha}",
                task.id,
            )
        try:
            specialist = specialist_by_id(decision.specialist_id)
        except RoutingError as exc:
            return self._blocked(mission_id, "routing_failed", str(exc), task.id)

        previous = task_record.attempts[-1] if task_record.attempts else None
        remediation = remediation_context(previous) if previous is not None else ""
        try:
            # A fresh workspace at the CURRENT frontier - never the failed one.
            workspace = repo.create_task_workspace(
                task.id, self._workspaces_root, baseline_sha=frontier
            )
        except DeliverableError as exc:
            return self._blocked(mission_id, "workspace_failed", str(exc), task.id)

        self._store.record_task_started(
            mission_id,
            task.id,
            baseline_sha=frontier,
            specialist_id=decision.specialist_id,
            remediation=remediation,
            workspace_dir=workspace.path.name,
            branch=workspace.branch,
        )
        attempt_number = len(self._store.load(mission_id).task(task.id).attempts)
        self._emit(
            "TASK_ATTEMPT_STARTED",
            {
                "mission_id": mission_id,
                "task_id": task.id,
                "attempt": attempt_number,
                "specialist_id": decision.specialist_id,
                "baseline_sha": frontier,
                "remediated": bool(remediation),
            },
        )
        try:
            request = TaskAttemptRequest(
                mission_id=mission_id,
                task_id=task.id,
                attempt=attempt_number,
                baseline_sha=frontier,
                workspace_dir=workspace.path.name,
                branch=workspace.branch,
                workspaces_root=str(self._workspaces_root),
                task=task,
                decision=decision,
                validation_policy=self._validation_policy,
                objective_summary=self._objective_summary(mission_id),
                dependency_summary=self._dependency_summary(accepted),
                remediation_summary=remediation,
            )
        except TransportError as exc:
            return self._blocked(mission_id, "task_request_invalid", str(exc), task.id)
        try:
            result = self._transport.execute(request)
        except TaskRunnerError as exc:
            return self._blocked(mission_id, "task_runner_error", str(exc), task.id)
        except TransportError as exc:
            # A transport that cannot deliver a trustworthy result yields a
            # failed ATTEMPT, counted by the same bounded budget as any other.
            result = transport_failure(request, str(exc))
        if result.task_id != task.id or result.baseline_sha != frontier:
            return self._blocked(
                mission_id,
                "result_identity_mismatch",
                f"result {result.task_id}@{result.baseline_sha} is not this attempt "
                f"{task.id}@{frontier}",
                task.id,
            )
        del specialist

        self._store.record_task_result(mission_id, result)
        self._emit(
            "TASK_ATTEMPT_FINISHED",
            {
                "mission_id": mission_id,
                "task_id": task.id,
                "attempt": attempt_number,
                "status": result.status,
            },
        )
        if result.status != STATUS_ACCEPTED:
            # No integration of failed work, ever. The caller decides whether
            # the bound permits another attempt.
            return None
        return self._integrate(
            mission_id, task, repo, self._store.load(mission_id).task(task.id)
        )

    def _integrate(
        self,
        mission_id: str,
        task: TaskContract,
        repo: DeliverableRepo,
        task_record: Any,
    ) -> MissionRunResult | None:
        """Advance the frontier exactly once for an accepted candidate.

        Git is the authority on whether the candidate already landed: a crash
        between the fast-forward and the ledger write leaves the commit in the
        deliverable, and re-fetching it would be a no-op at best, so the
        integration is reconciled rather than repeated.
        """
        candidate = task_record.candidate_sha
        if not candidate:
            return self._blocked(
                mission_id, "candidate_missing", f"task {task.id} has no candidate", task.id
            )
        try:
            if repo.contains(candidate):
                # Already landed (a crash between the fast-forward and the
                # ledger write). The frontier is the candidate itself.
                frontier_sha = candidate
            else:
                workspace = self._workspace_for(task, task_record)
                # The workspace is preserved on disk between attempts and
                # across restarts, so its branch tip is not self-evidently the
                # commit that was reviewed. Integrate only that exact commit.
                tip = workspace.candidate_sha()
                if tip != candidate:
                    return self._blocked(
                        mission_id,
                        "candidate_mismatch",
                        f"workspace branch tip {tip} is not the accepted candidate "
                        f"{candidate}",
                        task.id,
                    )
                frontier_sha = repo.integrate_candidate(workspace)
                if frontier_sha != candidate:
                    return self._blocked(
                        mission_id,
                        "integration_diverged",
                        f"integration produced {frontier_sha}, not the accepted "
                        f"candidate {candidate}",
                        task.id,
                    )
        except DeliverableError as exc:
            return self._blocked(mission_id, "integration_failed", str(exc), task.id)
        self._store.record_frontier_advanced(mission_id, task.id, frontier_sha=frontier_sha)
        self._emit(
            "FRONTIER_ADVANCED",
            {"mission_id": mission_id, "task_id": task.id, "frontier_sha": frontier_sha},
        )
        return None

    def _workspace_for(self, task: TaskContract, task_record: Any) -> TaskWorkspace:
        """Rebuild the workspace handle of the accepted attempt."""
        attempt = task_record.accepted_attempt
        if attempt is None or not attempt.workspace_dir:
            raise DeliverableError(f"task {task.id} has no recorded workspace to integrate")
        path = self._workspaces_root / attempt.workspace_dir
        if not (path / ".git").is_dir():
            raise DeliverableError(f"workspace {attempt.workspace_dir} is no longer available")
        return TaskWorkspace(
            task_id=task.id,
            path=path,
            baseline_sha=attempt.baseline_sha,
            branch=attempt.branch,
        )

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
