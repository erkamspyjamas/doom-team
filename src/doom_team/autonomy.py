"""Autonomous mission progression runner.

Implements planning/next-runnable-work continuation for a mission with
dependencies. Tasks execute automatically in dependency order with isolated
workspaces. Human-attention events are recorded only for the root-cause
failure of a dependency chain; blocked dependents do not record their own
attention event.
"""

from __future__ import annotations

import datetime
import subprocess
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from doom_team.domain import Attempt, Candidate, Evidence, Mission, TaskContract
from doom_team.ports import InferenceTelemetryPort, RepositoryPort, WorkspacePort
from doom_team.qa_reviewer import run_validation
from doom_team.verdicts import Verdict, VerdictStore


class TaskRunner(Protocol):
    """Executes a single task in a workspace and returns a candidate commit."""

    def __call__(self, task: TaskContract, workspace_id: str) -> str:
        """Run the task in the given workspace, returning the candidate commit."""
        ...


@dataclass
class TaskOutcome:
    """Outcome of executing a single task in the mission."""

    task_id: str
    candidate: Candidate | None
    evidence: Evidence | None
    qa_verdict: Verdict | None
    reviewer_verdict: Verdict | None
    error: str | None = None


class AttentionTracker:
    """Tracks human-attention events for failures that block progress."""

    def __init__(self) -> None:
        self._events: list[dict[str, str]] = []

    def record(self, task_id: str, reason: str) -> None:
        """Record a single attention event for a task."""
        self._events.append({"task_id": task_id, "reason": reason})

    def metrics(self) -> dict[str, object]:
        """Return total events and a per-task event count map."""
        per_task: dict[str, int] = {}
        for event in self._events:
            task_id = event["task_id"]
            per_task[task_id] = per_task.get(task_id, 0) + 1
        return {"total_events": len(self._events), "per_task": per_task}


class AutonomousMissionRunner:
    """Runs a mission automatically, respecting dependencies.

    Tasks are processed in dependency order. A task is run only when all of
    its dependencies have completed successfully. If a task fails (runner
    exception or failed validation), it is recorded as failed and a single
    human-attention event is emitted for that root cause. Dependent tasks are
    never executed and do not emit their own attention events.
    """

    def __init__(
        self,
        workspace_port: WorkspacePort,
        repository_port: RepositoryPort,
        verdict_store: VerdictStore,
        telemetry: InferenceTelemetryPort,
        runner: TaskRunner,
        qa_verify: Callable[[Candidate, Evidence], tuple[bool, str]],
        reviewer_verify: Callable[[Candidate, Evidence], tuple[bool, str]],
        model: str = "deepseek-v4",
        runtime: str = "local",
    ) -> None:
        self._workspace = workspace_port
        self._repository = repository_port
        self._verdict_store = verdict_store
        self._telemetry = telemetry
        self._runner = runner
        self._qa_verify = qa_verify
        self._reviewer_verify = reviewer_verify
        self._model = model
        self._runtime = runtime
        self.attention = AttentionTracker()

    def run(self, mission: Mission) -> dict[str, TaskOutcome]:
        """Execute all runnable tasks respecting dependencies.

        Returns a mapping of task id to outcome. If a task fails, its
        dependents are never executed. The method stops when no further
        progress can be made (all tasks complete or a failure blocks the
        remainder).
        """
        deps: dict[str, set[str]] = {t.id: set(t.dependencies) for t in mission.tasks}
        completed: set[str] = set()
        failed: set[str] = set()
        outcomes: dict[str, TaskOutcome] = {}

        while True:
            ready = [
                t
                for t in mission.tasks
                if t.id not in completed
                and t.id not in failed
                and deps[t.id].issubset(completed)
            ]
            if not ready:
                if failed or len(completed) == len(mission.tasks):
                    break
                raise RuntimeError("Dependency graph stalled; possible cycle")

            # Execute ready tasks. Concurrency is logical; tasks run sequentially
            # but in dependency-safe order.
            for task in ready:
                outcome = self._execute_task(mission, task)
                outcomes[task.id] = outcome
                if self._is_success(outcome):
                    completed.add(task.id)
                else:
                    failed.add(task.id)
                    self.attention.record(task.id, outcome.error or "validation failed")

        return outcomes

    def _is_success(self, outcome: TaskOutcome) -> bool:
        """Return True only if the task completed with passing QA/Reviewer."""
        return (
            outcome.error is None
            and outcome.qa_verdict is not None
            and outcome.qa_verdict.decision is True
            and outcome.reviewer_verdict is not None
            and outcome.reviewer_verdict.decision is True
        )

    def _execute_task(self, mission: Mission, task: TaskContract) -> TaskOutcome:
        """Run one task end-to-end with isolated workspace and evidence."""
        workspace_id = self._workspace.create_workspace(task.id)
        try:
            baseline_commit = self._repository.baseline_commit(workspace_id)
            candidate_commit = self._runner(task, workspace_id)
            attempt = Attempt(id=f"{task.id}_a0", task_id=task.id, recovery_epoch=0)
            candidate = Candidate(
                id=f"cand_{uuid.uuid4().hex[:8]}",
                task_id=task.id,
                attempt=attempt,
                baseline_commit=baseline_commit,
                candidate_commit=candidate_commit,
                workspace_id=workspace_id,
            )

            evidence = Evidence(
                id=f"ev_{uuid.uuid4().hex[:8]}",
                candidate=candidate,
                objective_id=mission.objective.id,
                mission_id=mission.id,
                producer_role="dev",
                producer_run=f"run_{uuid.uuid4().hex[:8]}",
                command="make test",
                artifacts=(),
                actual_model=self._model,
                actual_runtime=self._runtime,
                timestamp=datetime.datetime.now(datetime.timezone.utc),
                digest=f"sha256:{candidate_commit}",
            )

            self._telemetry.record("default", self._model, self._runtime)

            qa_verdict = run_validation(
                candidate=candidate,
                evidence=evidence,
                role="qa",
                producer_run=evidence.producer_run,
                verdict_store=self._verdict_store,
                verify=self._qa_verify,
            )
            reviewer_verdict = run_validation(
                candidate=candidate,
                evidence=evidence,
                role="reviewer",
                producer_run=evidence.producer_run,
                verdict_store=self._verdict_store,
                verify=self._reviewer_verify,
            )

            return TaskOutcome(
                task_id=task.id,
                candidate=candidate,
                evidence=evidence,
                qa_verdict=qa_verdict,
                reviewer_verdict=reviewer_verdict,
            )
        except (ValueError, OSError, subprocess.CalledProcessError) as exc:
            return TaskOutcome(
                task_id=task.id,
                candidate=None,
                evidence=None,
                qa_verdict=None,
                reviewer_verdict=None,
                error=str(exc),
            )
