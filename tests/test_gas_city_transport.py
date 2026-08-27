"""Real Gas City transport: one task attempt crosses a real Gas City boundary.

Nothing here fakes Gas City. Each attempt creates a real work item in a real
(disposable) city through the installed `gc` binary, and the worker must
claim that exact item through `gc hook --claim` before it is allowed to do
anything. Only the model is deterministic: a local stub endpoint serves the
canonical OpenAI-compatible client, so no provider network call happens.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest

from doom_team.deliverable import DeliverableRepo
from doom_team.gas_city_transport import GasCityTaskTransport
from doom_team.mission import MISSION_AWAITING_HUMAN_APPROVAL, MISSION_BLOCKED, MissionStore
from doom_team.mission_runner import MissionRunner
from tests.test_mission_recovery import BROKEN_CODE, CODE, DOCS, GOOD_CODE

GC_BINARY = Path("/home/eko/doom/autopilot/.tools/bin/gc")
GC_TOOLS = GC_BINARY.parent
API_KEY = "t7-local-stub-key"

pytestmark = pytest.mark.skipif(
    not GC_BINARY.exists() or shutil.which("dolt", path=str(GC_TOOLS)) is None,
    reason="the installed Gas City toolchain is required for the real transport test",
)

DOCS_FILES = {"README.md": "# Arithmetic\n\nRun: python -m pytest\n"}

PLAN_TASKS = [
    {
        "task_id": "arith-code",
        "description": CODE,
        "dependencies": [],
        "required_capabilities": ["backend.python"],
        "acceptance_criteria": ["add and multiply behave correctly"],
    },
    {
        "task_id": "arith-docs",
        "description": DOCS,
        "dependencies": ["arith-code"],
        "required_capabilities": ["documentation"],
        "acceptance_criteria": ["README documents usage"],
    },
]
PASS_VERDICT = json.dumps({"verdict": "pass", "summary": "looks correct", "blockers": []})
BLOCK_VERDICT = json.dumps(
    {"verdict": "block", "summary": "defect found", "blockers": ["missing edge case"]}
)


class StubModel:
    """A deterministic OpenAI-compatible endpoint. No provider network call."""

    def __init__(self, scripts: dict[str, list[dict[str, Any]]]) -> None:
        self.scripts = scripts
        self.counts: dict[str, int] = {}
        self.current: dict[str, Any] = {}
        self.implementer_systems: list[str] = []
        self.lock = threading.Lock()

    def reply(self, messages: list[dict[str, str]]) -> str:
        system, user = messages[0]["content"], messages[1]["content"]
        with self.lock:
            if "mission planner" in system:
                return json.dumps({"tasks": PLAN_TASKS})
            if "implementation specialist for exactly one" in system:
                for marker, seq in self.scripts.items():
                    if marker in user:
                        index = min(self.counts.get(marker, 0), len(seq) - 1)
                        self.counts[marker] = self.counts.get(marker, 0) + 1
                        self.current = seq[index]
                        self.implementer_systems.append(system)
                        return json.dumps(
                            {
                                "files": [
                                    {"path": p, "content": c}
                                    for p, c in self.current["files"].items()
                                ],
                                "summary": "implement the task",
                            }
                        )
                raise AssertionError(f"no script for {user[:120]}")
            if "independent QA engineer" in system:
                return str(self.current.get("qa", PASS_VERDICT))
            if "independent final reviewer" in system:
                return str(self.current.get("reviewer", PASS_VERDICT))
        raise AssertionError("unexpected role")


def _serve(model: StubModel) -> tuple[HTTPServer, str]:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length))
            try:
                content = model.reply(payload["messages"])
                body = json.dumps({"choices": [{"message": {"content": content}}]})
                code = 200
            except Exception as exc:  # surfaced to the client as a provider error
                body = json.dumps({"error": str(exc)})
                code = 500
            raw = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args: Any) -> None:
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}"


def _make_city(root: Path) -> Path:
    """Create a disposable, isolated Gas City. Never touches existing cities."""
    city = root / "city"
    city.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, PATH=f"{GC_TOOLS}:{os.environ.get('PATH', '')}")
    completed = subprocess.run(
        [
            str(GC_BINARY), "init", "--template", "minimal", "--default-provider", "claude",
            "--skip-provider-readiness", "--no-start", "--yes", "--json",
            "--name", "t7city", str(city),
        ],
        cwd=str(root), capture_output=True, text=True, timeout=600, env=env,
    )
    payload = json.loads(completed.stdout.strip().splitlines()[-1])
    if not payload.get("ok"):
        pytest.skip(f"disposable gas city unavailable: {completed.stderr[:200]}")
    return city


@pytest.fixture(scope="module")
def gas_city(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _make_city(tmp_path_factory.mktemp("gascity"))


def _beads(city: Path) -> list[dict[str, Any]]:
    env = dict(os.environ, PATH=f"{GC_TOOLS}:{os.environ.get('PATH', '')}")
    completed = subprocess.run(
        [str(GC_BINARY), "bd", "--city", str(city), "list", "--all", "--json"],
        cwd=str(city), capture_output=True, text=True, timeout=120, env=env,
    )
    try:
        data = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else data.get("issues", [])


def _mission(
    tmp_path: Path,
    city: Path,
    scripts: dict[str, list[dict[str, Any]]],
    *,
    semantic_retries: int = 1,
    timeout: float = 600.0,
) -> tuple[Any, MissionStore, StubModel, GasCityTaskTransport]:
    model = StubModel(scripts)
    server, base_url = _serve(model)
    try:
        env = dict(
            os.environ,
            PATH=f"{GC_TOOLS}:{os.environ.get('PATH', '')}",
            DOOM_API_KEY=API_KEY,
            DOOM_STATE_DIR=str(tmp_path / "state"),
            DOOM_MODEL_PROVIDER="nvidia",
            DOOM_MODEL_NAME="stub-model",
            DOOM_PROVIDER_BASE_URL=base_url,
            PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"),
        )
        transport = GasCityTaskTransport(
            city=city,
            gc_binary=GC_BINARY,
            jobs_root=tmp_path / "jobs",
            agent="mayor",
            timeout=timeout,
            worker_env=env,
            secret_values=(API_KEY,),
        )
        store = MissionStore(tmp_path / "state")
        from doom_team.inference import build_inference_client
        from doom_team.config import ConfigLoader

        settings = ConfigLoader().load(
            state_dir=str(tmp_path / "state"),
            model_provider="nvidia",
            model_name="stub-model",
            provider_base_url=base_url,
        )
        runner = MissionRunner(
            build_inference_client(settings),
            store,
            workspaces_root=tmp_path / "work",
            validation_commands=__import__(
                "doom_team.transport", fromlist=["x"]
            ).VALIDATION_POLICIES["default"],
            semantic_retries=semantic_retries,
            transport=transport,
            validation_policy="default",
        )
        result = runner.run("Create a tiny arithmetic package", tmp_path / "project")
        return result, store, model, transport
    finally:
        server.shutdown()


# --- T7-O: the core real-transport acceptance -----------------------------


def test_real_gas_city_executes_task_attempts(
    tmp_path: Path, gas_city: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOOM_API_KEY", API_KEY)
    before = len(_beads(gas_city))
    result, store, model, transport = _mission(
        tmp_path,
        gas_city,
        {CODE: [{"files": GOOD_CODE}], DOCS: [{"files": DOCS_FILES}]},
    )

    assert result.status == MISSION_AWAITING_HUMAN_APPROVAL
    assert all(t.integrated for t in result.record.tasks)

    # Real Gas City work items were created, routed, and completed - one per
    # attempt, each carrying this mission's identity.
    after = _beads(gas_city)
    mine = [b for b in after if result.mission_id in str(b.get("title", ""))]
    assert len(mine) == 2, [b.get("title") for b in after]
    titles = [str(b.get("title", "")) for b in mine]
    assert any("arith-code" in t for t in titles)
    assert any("arith-docs" in t for t in titles)
    # Gas City recorded each item as completed work, released for the next.
    assert all(str(b.get("status")) == "closed" for b in mine), mine
    assert len(after) >= before

    # The worker really ran out-of-process, claimed its item, and returned a
    # canonical result for each attempt.
    for task_id in ("arith-code", "arith-docs"):
        job = tmp_path / "jobs" / result.mission_id / task_id / "attempt-1"
        envelope = json.loads((job / "request.json").read_text())
        body = json.loads((job / "result.json").read_text())
        provenance = json.loads((job / "transport.json").read_text())
        assert envelope["gas_city"]["bead_id"].startswith("t7")
        assert body["mission_id"] == result.mission_id
        assert body["attempt"] == 1
        assert provenance["terminal_state"] == "completed"
        assert provenance["job_id"] == envelope["gas_city"]["bead_id"]

    # T2 routing survived the transport: two different specialists ran.
    specialists = {i["specialist_id"] for i in result.record.routing}
    assert specialists == {"python_backend", "technical_writer"}
    assert len(model.implementer_systems) == 2
    assert "backend implementation specialist" in model.implementer_systems[0]
    assert "documentation specialist" in model.implementer_systems[1]

    # Real work landed in the deliverable, and nothing was published.
    repo = DeliverableRepo.open(tmp_path / "project")
    assert (repo.path / "arith.py").exists() and (repo.path / "README.md").exists()
    assert repo.remotes() == ()
    assert result.record.approval is None  # human approval still required


def test_secrets_never_cross_the_gas_city_boundary(
    tmp_path: Path, gas_city: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOOM_API_KEY", API_KEY)
    result, store, _, _ = _mission(
        tmp_path, gas_city, {CODE: [{"files": GOOD_CODE}], DOCS: [{"files": DOCS_FILES}]}
    )
    job_root = tmp_path / "jobs"
    for path in job_root.rglob("*.json"):
        assert API_KEY not in path.read_text(), path
    ledger = (tmp_path / "state" / "missions" / f"{result.mission_id}.json").read_text()
    assert API_KEY not in ledger
    assert API_KEY not in json.dumps(result.as_dict())
    # Nor in Gas City's own work items.
    assert API_KEY not in json.dumps(_beads(gas_city))


# --- T7-Q: T6 still owns every retry --------------------------------------


def test_bounded_recovery_over_gas_city(
    tmp_path: Path, gas_city: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOOM_API_KEY", API_KEY)
    result, store, model, _ = _mission(
        tmp_path,
        gas_city,
        {
            CODE: [{"files": GOOD_CODE, "qa": BLOCK_VERDICT}, {"files": GOOD_CODE}],
            DOCS: [{"files": DOCS_FILES}],
        },
    )
    assert result.status == MISSION_AWAITING_HUMAN_APPROVAL
    code = result.record.task("arith-code")
    assert [a.status for a in code.attempts] == ["qa_blocked", "accepted"]
    # Attempt 2 was a NEW Gas City job, not a resubmission of the first.
    first = json.loads(
        (tmp_path / "jobs" / result.mission_id / "arith-code" / "attempt-1" / "request.json").read_text()
    )
    second = json.loads(
        (tmp_path / "jobs" / result.mission_id / "arith-code" / "attempt-2" / "request.json").read_text()
    )
    assert first["gas_city"]["bead_id"] != second["gas_city"]["bead_id"]
    assert second["remediation_summary"]  # the retry was told what went wrong


def test_exhaustion_over_gas_city_stops_creating_jobs(
    tmp_path: Path, gas_city: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOOM_API_KEY", API_KEY)
    result, store, model, _ = _mission(
        tmp_path, gas_city, {CODE: [{"files": BROKEN_CODE}], DOCS: [{"files": DOCS_FILES}]}
    )
    assert result.status == MISSION_BLOCKED
    assert result.record.blocker["reason"] == "recovery_exhausted"
    code = result.record.task("arith-code")
    assert len(code.attempts) == result.record.max_attempts_per_task == 2
    # Exactly two Gas City jobs for that task, and none for the dependent task.
    jobs = tmp_path / "jobs" / result.mission_id
    assert sorted(p.name for p in (jobs / "arith-code").iterdir()) == [
        "attempt-1",
        "attempt-2",
    ]
    assert not (jobs / "arith-docs").exists()


# --- Integrity-review regressions -----------------------------------------


def test_a_stale_work_item_does_not_poison_later_attempts(
    tmp_path: Path, gas_city: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An item left open by an earlier crash must not be handed to this worker."""
    monkeypatch.setenv("DOOM_API_KEY", API_KEY)
    env = dict(os.environ, PATH=f"{GC_TOOLS}:{os.environ.get('PATH', '')}")
    # Leave an orphaned, routed work item assigned to the agent.
    subprocess.run(
        [str(GC_BINARY), "sling", "--city", str(gas_city), "--json", "--no-convoy",
         "mayor", "orphaned item from a crashed run"],
        cwd=str(gas_city), capture_output=True, text=True, timeout=180, env=env,
    )
    subprocess.run(
        [str(GC_BINARY), "hook", "--city", str(gas_city), "--claim", "--json", "mayor"],
        cwd=str(gas_city), capture_output=True, text=True, timeout=180, env=env,
    )

    result, _, _, _ = _mission(
        tmp_path, gas_city, {CODE: [{"files": GOOD_CODE}], DOCS: [{"files": DOCS_FILES}]}
    )
    # The mission still completes: the stale assignment was released first.
    assert result.status == MISSION_AWAITING_HUMAN_APPROVAL
    assert all(len(t.attempts) == 1 for t in result.record.tasks)


def test_every_work_item_is_completed_even_when_the_transport_fails(
    tmp_path: Path, gas_city: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed attempt must never leave an item holding the agent's queue."""
    monkeypatch.setenv("DOOM_API_KEY", API_KEY)
    from doom_team.gas_city_transport import GasCityTaskTransport
    from doom_team.routing import route_task
    from doom_team.transport import TaskAttemptRequest
    from tests.test_mission_recovery import SINGLE_PLAN  # noqa: F401
    from doom_team.domain import TaskContract

    task = TaskContract(
        id="arith-code", objective_id="o", instruction=CODE,
        acceptance_criteria=("c",), required_capabilities=("backend.python",),
    )
    workspaces = tmp_path / "work"
    workspaces.mkdir(parents=True)
    (workspaces / "ws-1").mkdir()
    transport = GasCityTaskTransport(
        city=gas_city,
        gc_binary=GC_BINARY,
        jobs_root=tmp_path / "jobs",
        # A worker that always fails: the item must still be completed.
        worker_argv=("/bin/false",),
    )
    request = TaskAttemptRequest(
        mission_id="mission-cccccccccccc-33334444", task_id="arith-code", attempt=1,
        baseline_sha="a" * 40, workspace_dir="ws-1", branch="task/arith-code",
        workspaces_root=str(workspaces), task=task, decision=route_task(task),
    )
    result = transport.execute(request)
    assert result.status == "execution_failed"

    # The agent's queue is clean: the next claim finds no leftover work.
    env = dict(os.environ, PATH=f"{GC_TOOLS}:{os.environ.get('PATH', '')}")
    completed = subprocess.run(
        [str(GC_BINARY), "hook", "--city", str(gas_city), "--claim", "--json", "mayor"],
        cwd=str(gas_city), capture_output=True, text=True, timeout=180, env=env,
    )
    payload = json.loads(completed.stdout)
    assert payload.get("reason") != "existing_assignment", payload


def test_local_io_failure_is_one_typed_attempt_not_a_crash(
    tmp_path: Path, gas_city: Path
) -> None:
    """An unwritable job directory fails the attempt, it does not kill the engine."""
    from doom_team.gas_city_transport import GasCityTaskTransport
    from doom_team.routing import route_task
    from doom_team.transport import TaskAttemptRequest
    from doom_team.domain import TaskContract

    jobs = tmp_path / "jobs"
    jobs.mkdir()
    jobs.chmod(0o500)
    try:
        task = TaskContract(
            id="arith-code", objective_id="o", instruction=CODE,
            acceptance_criteria=("c",), required_capabilities=("backend.python",),
        )
        workspaces = tmp_path / "work"
        workspaces.mkdir()
        (workspaces / "ws-1").mkdir()
        transport = GasCityTaskTransport(
            city=gas_city, gc_binary=GC_BINARY, jobs_root=jobs
        )
        result = transport.execute(
            TaskAttemptRequest(
                mission_id="mission-dddddddddddd-55556666", task_id="arith-code",
                attempt=1, baseline_sha="a" * 40, workspace_dir="ws-1",
                branch="task/arith-code", workspaces_root=str(workspaces),
                task=task, decision=route_task(task),
            )
        )
        assert result.status == "execution_failed"
        assert "transport failure" in result.failure_detail
    finally:
        jobs.chmod(0o700)
