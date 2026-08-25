"""Reliability and security invariant tests.

Proves a 20+ task mission completes with zero stuck work, no workspace
escape, fresh candidate-bound evidence, and no duplicate evidence. These
are the acceptance invariants that guard against unexplained stalls,
unsafe mutations, stale acceptance and unbounded duplicate growth.
"""

from pathlib import Path

from doom_team.domain import Mission, Objective, TaskContract
from doom_team.parallel import ParallelMissionRunner
from doom_team.telemetry import InMemoryTelemetry
from doom_team.verdicts import InMemoryVerdictStore
from doom_team.workspace import GitWorkspace


def _add_file_patch(file_name: str, content: str) -> bytes:
    """Build a git patch that adds a single new file."""
    return (
        f"diff --git a/{file_name} b/{file_name}\n"
        f"new file mode 100644\n"
        f"index 0000000..e69de29\n"
        f"--- /dev/null\n"
        f"+++ b/{file_name}\n"
        f"@@ -0,0 +1 @@\n"
        f"+{content}\n"
    ).encode()


def _build_mission(num_tasks: int) -> Mission:
    """Build a linear chain mission with the given number of tasks."""
    objective = Objective(id="obj-1", description="Reliability endurance mission")
    tasks = []
    for i in range(num_tasks):
        task_id = f"task-{i}"
        deps = (f"task-{i - 1}",) if i > 0 else ()
        tasks.append(
            TaskContract(
                id=task_id,
                objective_id="obj-1",
                instruction=f"Implement {task_id}",
                acceptance_criteria=(f"criterion {task_id}",),
                dependencies=deps,
            )
        )
    return Mission(id="mission-1", objective=objective, tasks=tuple(tasks))


def _run_task(ws: GitWorkspace, task: TaskContract, workspace_id: str) -> str:
    """Write a task-specific file and commit it in the workspace."""
    file_name = f"file_{task.id}.txt"
    ws.apply_patch(workspace_id, _add_file_patch(file_name, f"content {task.id}"))
    return ws.commit(workspace_id, f"task {task.id}")


def _qa_verify(ws: GitWorkspace, candidate, evidence) -> tuple[bool, str]:
    """Read-only QA check: the task file exists in the workspace."""
    path = ws._workspace_path(candidate.workspace_id)
    if (path / f"file_{candidate.task_id}.txt").exists():
        return True, "ok"
    return False, "missing file"


def test_20_plus_task_endurance(tmp_path: Path) -> None:
    """Run 21 tasks through the full pipeline with zero stuck/unsafe/stale/duplicate work."""
    ws = GitWorkspace(tmp_path / "workspaces")
    mission = _build_mission(21)
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

    # All tasks completed (no stuck work)
    assert len(outcomes) == 21
    assert set(outcomes.keys()) == {f"task-{i}" for i in range(21)}

    workspace_ids: set[str] = set()
    digests: set[str] = set()
    for outcome in outcomes.values():
        assert outcome.error is None
        assert outcome.candidate is not None
        assert outcome.evidence is not None
        assert outcome.qa_verdict is not None and outcome.qa_verdict.decision is True
        assert outcome.reviewer_verdict is not None and outcome.reviewer_verdict.decision is True

        # Fresh evidence bound to the exact candidate
        assert outcome.evidence.candidate.id == outcome.candidate.id
        assert outcome.evidence.candidate.candidate_commit == outcome.candidate.candidate_commit

        # No duplicate evidence
        assert outcome.evidence.digest not in digests
        digests.add(outcome.evidence.digest)

        # No shared workspace (isolated state)
        assert outcome.candidate.workspace_id not in workspace_ids
        workspace_ids.add(outcome.candidate.workspace_id)

        # No unsafe workspace escape
        assert ws.escape_check(outcome.candidate.workspace_id) is False
