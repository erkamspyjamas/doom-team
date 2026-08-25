"""Production candidate readiness test: full pipeline reaches READY_FOR_FINAL_APPROVAL."""

from pathlib import Path

from doom_team.domain import (
    Candidate,
    Evidence,
    Mission,
    Objective,
    SupervisorDecision,
    TaskContract,
)
from doom_team.parallel import ParallelMissionRunner
from doom_team.supervisor import Supervisor
from doom_team.telemetry import InMemoryTelemetry
from doom_team.verdicts import InMemoryVerdictStore
from doom_team.workspace import GitWorkspace


def _add_file_patch(file_name: str, content: str) -> bytes:
    return (
        f"diff --git a/{file_name} b/{file_name}\n"
        f"new file mode 100644\n"
        f"index 0000000..e69de29\n"
        f"--- /dev/null\n"
        f"+++ b/{file_name}\n"
        f"@@ -0,0 +1 @@\n"
        f"+{content}\n"
    ).encode()


def _build_mission() -> Mission:
    objective = Objective(id="obj-1", description="Production candidate mission")
    tasks = [
        TaskContract(
            id="task-1",
            objective_id="obj-1",
            instruction="Implement A",
            acceptance_criteria=("criterion A",),
        ),
        TaskContract(
            id="task-2",
            objective_id="obj-1",
            instruction="Implement B",
            acceptance_criteria=("criterion B",),
            dependencies=("task-1",),
        ),
        TaskContract(
            id="task-3",
            objective_id="obj-1",
            instruction="Implement C",
            acceptance_criteria=("criterion C",),
            dependencies=("task-2",),
        ),
    ]
    return Mission(id="mission-1", objective=objective, tasks=tuple(tasks))


def _run_task(ws: GitWorkspace, task: TaskContract, workspace_id: str) -> str:
    file_name = f"file_{task.id}.txt"
    ws.apply_patch(workspace_id, _add_file_patch(file_name, f"content {task.id}"))
    return ws.commit(workspace_id, f"task {task.id}")


def _qa_verify(ws: GitWorkspace, candidate: Candidate, evidence: Evidence) -> tuple[bool, str]:
    path = ws._workspace_path(candidate.workspace_id)
    if (path / f"file_{candidate.task_id}.txt").exists():
        return True, "ok"
    return False, "missing file"


def _objective_met(candidate: Candidate, evidence: Evidence) -> bool:
    return True


def test_production_candidate_reaches_ready_for_final_approval(tmp_path: Path) -> None:
    ws = GitWorkspace(tmp_path / "workspaces")
    mission = _build_mission()
    verdict_store = InMemoryVerdictStore()
    telemetry = InMemoryTelemetry()

    runner = ParallelMissionRunner(
        workspace_port=ws,
        repository_port=ws,
        verdict_store=verdict_store,
        telemetry=telemetry,
        runner=lambda task, ws_id: _run_task(ws, task, ws_id),
        qa_verify=lambda c, e: _qa_verify(ws, c, e),
        reviewer_verify=lambda c, e: (True, "ok"),
    )

    outcomes = runner.run(mission)

    supervisor = Supervisor()
    for outcome in outcomes.values():
        assert outcome.error is None
        assert outcome.candidate is not None
        assert outcome.evidence is not None
        qa_passed = outcome.qa_verdict.decision if outcome.qa_verdict else False
        reviewer_passed = (
            outcome.reviewer_verdict.decision if outcome.reviewer_verdict else False
        )
        decision, reason = supervisor.decide(
            candidate=outcome.candidate,
            evidence=outcome.evidence,
            qa_passed=qa_passed,
            reviewer_passed=reviewer_passed,
            objective_checker=_objective_met,
        )
        assert decision is SupervisorDecision.READY_FOR_FINAL_APPROVAL
        assert "ready" in reason

    # Without an approval port, the supervisor never authorizes a release.
    first_outcome = next(iter(outcomes.values()))
    assert first_outcome.candidate is not None
    assert first_outcome.evidence is not None
    decision, _ = supervisor.decide(
        candidate=first_outcome.candidate,
        evidence=first_outcome.evidence,
        qa_passed=True,
        reviewer_passed=True,
        objective_checker=_objective_met,
    )
    assert decision is SupervisorDecision.READY_FOR_FINAL_APPROVAL
