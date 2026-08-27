"""Gate B: the product runtime executing whole missions over real Gas City.

The mission is driven by the real `doom-team` CLI, every task attempt is a
real Gas City work item executed by a real out-of-process Doom Team worker,
and the only thing faked is the model - served through the canonical
inference client from a local endpoint, so no provider is called.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from doom_team.cli import main
from doom_team.config import TRANSPORT_GAS_CITY, TRANSPORT_IN_PROCESS, ConfigError, ConfigLoader
from doom_team.deliverable import DeliverableRepo
from doom_team.gas_city_transport import GasCityTaskTransport
from doom_team.mission import (
    MISSION_APPROVED,
    MISSION_AWAITING_HUMAN_APPROVAL,
    MissionStore,
)
from doom_team.runtime import RuntimeError_, build_task_execution_transport, ensure_gas_city
from doom_team.transport import InProcessTaskTransport
from tests.test_gas_city_transport import (
    API_KEY,
    BLOCK_VERDICT,
    GC_BINARY,
    GC_TOOLS,
    StubModel,
    _serve,
)
from tests.test_mission_recovery import CODE, DOCS, GOOD_CODE

pytestmark = pytest.mark.skipif(
    not GC_BINARY.exists(), reason="the installed Gas City toolchain is required"
)

TESTS = "Add edge-case tests"
DOCS_FILES = {"README.md": "# Arithmetic\n\nInstall: none needed.\nRun: python -m pytest\n"}
EDGE_TESTS = {
    "test_arith_edge.py": (
        "from arith import add, multiply\n\n\n"
        "def test_add_negative():\n    assert add(-1, -1) == -2\n\n\n"
        "def test_multiply_zero():\n    assert multiply(5, 0) == 0\n"
    )
}

# Three tasks, three capability sets, a real dependency chain.
GATE_B_PLAN = [
    {
        "task_id": "arith-code",
        "description": CODE,
        "dependencies": [],
        "required_capabilities": ["backend.python"],
        "acceptance_criteria": ["add and multiply behave correctly"],
    },
    {
        "task_id": "arith-tests",
        "description": TESTS,
        "dependencies": ["arith-code"],
        "required_capabilities": ["testing"],
        "acceptance_criteria": ["edge cases are covered"],
    },
    {
        "task_id": "arith-docs",
        "description": DOCS,
        "dependencies": ["arith-tests"],
        "required_capabilities": ["documentation"],
        "acceptance_criteria": ["README documents install and usage"],
    },
]


class GateBModel(StubModel):
    """Deterministic model for the Gate-B fixture, with one recoverable failure."""

    def __init__(self, scripts: dict[str, list[dict[str, Any]]]) -> None:
        super().__init__(scripts)
        self.plan_tasks = GATE_B_PLAN

    def reply(self, messages: list[dict[str, str]]) -> str:
        system = messages[0]["content"]
        if "mission planner" in system:
            return json.dumps({"tasks": self.plan_tasks})
        return super().reply(messages)


def _gas_city_env(tmp_path: Path, base_url: str, city: Path, **extra: str) -> dict[str, str]:
    env = {
        "DOOM_API_KEY": API_KEY,
        "DOOM_STATE_DIR": str(tmp_path / "state"),
        "DOOM_MODEL_PROVIDER": "nvidia",
        "DOOM_MODEL_NAME": "stub-model",
        "DOOM_PROVIDER_BASE_URL": base_url,
        "DOOM_TASK_TRANSPORT": TRANSPORT_GAS_CITY,
        "DOOM_GAS_CITY_BINARY": str(GC_BINARY),
        "DOOM_GAS_CITY_CITY": str(city),
        "DOOM_SEMANTIC_RETRIES": "1",
        "PATH": f"{GC_TOOLS}:{os.environ.get('PATH', '')}",
        "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
    }
    env.update(extra)
    return env


def _apply(monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> None:
    for key, value in env.items():
        monkeypatch.setenv(key, value)


def _beads(city: Path) -> list[dict[str, Any]]:
    env = dict(os.environ, PATH=f"{GC_TOOLS}:{os.environ.get('PATH', '')}")
    completed = subprocess.run(
        [str(GC_BINARY), "bd", "--city", str(city), "list", "--all", "--json"],
        cwd=str(city), capture_output=True, text=True, timeout=180, env=env,
    )
    try:
        data = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else data.get("issues", [])


# --- T8-A: transport selection is centralized, explicit and fail-closed ----


def test_transport_selection_follows_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOOM_API_KEY", API_KEY)
    settings = ConfigLoader().load(
        state_dir=str(tmp_path / "state"), model_provider="nvidia",
        model_name="m", provider_base_url="http://127.0.0.1:1",
    )
    assert settings.task_transport == TRANSPORT_IN_PROCESS
    transport = build_task_execution_transport(settings, object())  # type: ignore[arg-type]
    assert isinstance(transport, InProcessTaskTransport)

    gas = ConfigLoader().load(
        state_dir=str(tmp_path / "state"), model_provider="nvidia", model_name="m",
        provider_base_url="http://127.0.0.1:1", task_transport=TRANSPORT_GAS_CITY,
        gas_city_binary=str(GC_BINARY), gas_city_city=str(tmp_path / "city"),
    )
    assert gas.task_transport == TRANSPORT_GAS_CITY
    assert isinstance(
        build_task_execution_transport(gas, object()),  # type: ignore[arg-type]
        GasCityTaskTransport,
    )


@pytest.mark.parametrize(
    "overrides,match",
    [
        ({}, "gas_city_binary is required"),
        ({"gas_city_binary": "/nonexistent/gc"}, "not executable"),
        ({"gas_city_binary": "relative/gc"}, "absolute path"),
    ],
)
def test_gas_city_configuration_fails_closed(
    tmp_path: Path, overrides: dict[str, str], match: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DOOM_API_KEY", API_KEY)
    fields: dict[str, Any] = dict(
        state_dir=str(tmp_path / "state"), model_provider="nvidia", model_name="m",
        provider_base_url="http://127.0.0.1:1", task_transport=TRANSPORT_GAS_CITY,
        gas_city_city=str(tmp_path / "city"),
    )
    fields.update(overrides)
    with pytest.raises(ConfigError, match=match):
        ConfigLoader().load(**fields)


def test_unknown_transport_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOOM_API_KEY", API_KEY)
    with pytest.raises(ConfigError, match="task_transport must be one of"):
        ConfigLoader().load(
            state_dir=str(tmp_path / "state"), model_provider="nvidia", model_name="m",
            provider_base_url="http://127.0.0.1:1", task_transport="ssh-into-prod",
        )


def test_configured_gas_city_never_falls_back_to_in_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A broken Gas City is an error, never a quiet switch to local execution."""
    monkeypatch.setenv("DOOM_API_KEY", API_KEY)
    blocked = tmp_path / "not-a-city"
    blocked.mkdir()
    (blocked / "occupied.txt").write_text("x")
    settings = ConfigLoader().load(
        state_dir=str(tmp_path / "state"), model_provider="nvidia", model_name="m",
        provider_base_url="http://127.0.0.1:1", task_transport=TRANSPORT_GAS_CITY,
        gas_city_binary=str(GC_BINARY), gas_city_city=str(blocked),
    )
    with pytest.raises(RuntimeError_, match="not a city"):
        build_task_execution_transport(settings, object())  # type: ignore[arg-type]


def test_standalone_city_is_initialized_once_and_reused(tmp_path: Path) -> None:
    city = tmp_path / "doom-team-city"
    first = ensure_gas_city(city, GC_BINARY)
    assert (first / "city.toml").exists()
    stamp = (first / "city.toml").stat().st_mtime_ns
    assert ensure_gas_city(city, GC_BINARY) == first
    assert (first / "city.toml").stat().st_mtime_ns == stamp  # not re-initialized
    # The city lives where configured - never inside a deliverable repo.
    assert city.is_relative_to(tmp_path)


# --- T8-N: plan-only stays ephemeral even with Gas City configured ---------


def test_plan_only_has_no_gas_city_side_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    model = GateBModel({})
    server, base_url = _serve(model)
    try:
        city = tmp_path / "city"
        _apply(monkeypatch, _gas_city_env(tmp_path, base_url, city))
        assert main(["run", "Build a tiny arithmetic package", "--plan-only"]) == 0
        output = json.loads(capsys.readouterr().out)
        assert len(output["tasks"]) == 3
    finally:
        server.shutdown()
    # No city, no mission state, no deliverable, no jobs.
    assert not city.exists()
    assert not (tmp_path / "state").exists()


# --- T8-W / Gate B: the whole mission through the product CLI --------------


@pytest.fixture(scope="module")
def gate_b(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """Run ONE full mission through the real CLI over real Gas City."""
    tmp_path = tmp_path_factory.mktemp("gateb")
    model = GateBModel(
        {
            CODE: [{"files": GOOD_CODE}],
            # One recoverable failure, then success - bounded by T6.
            TESTS: [{"files": EDGE_TESTS, "qa": BLOCK_VERDICT}, {"files": EDGE_TESTS}],
            DOCS: [{"files": DOCS_FILES}],
        }
    )
    server, base_url = _serve(model)
    city = tmp_path / "doom-team-city"
    env = _gas_city_env(tmp_path, base_url, city)
    previous = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        rc = main(
            [
                "run",
                "Create a tiny Python arithmetic package with tests and a README",
                "--destination",
                str(tmp_path / "project"),
            ]
        )
        from doom_team.cli import _load_settings  # noqa: F401  (settings come from env)

        store = MissionStore(tmp_path / "state")
        mission_id = store.list_ids()[0]
        return {
            "rc": rc,
            "tmp_path": tmp_path,
            "city": city,
            "mission_id": mission_id,
            "record": store.load(mission_id),
            "model": model,
            "env": env,
        }
    finally:
        server.shutdown()
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def test_gate_b_full_mission_reaches_approval_gate(gate_b: dict[str, Any]) -> None:
    record = gate_b["record"]
    assert gate_b["rc"] == 0
    assert record.status == MISSION_AWAITING_HUMAN_APPROVAL
    assert len(record.tasks) == 3
    assert all(t.integrated for t in record.tasks)
    # The frontier advanced once per task, ending where the mission ends.
    integrated = [record.task(t).integrated_sha for t in
                  ("arith-code", "arith-tests", "arith-docs")]
    assert len(set(integrated)) == 3
    assert record.final_frontier_sha == integrated[-1]
    assert record.final_validation is not None and record.final_validation_passed
    assert record.approval is None  # a human still has to decide


def test_gate_b_every_attempt_was_a_real_gas_city_job(gate_b: dict[str, Any]) -> None:
    record, city, tmp_path = gate_b["record"], gate_b["city"], gate_b["tmp_path"]
    beads = _beads(city)
    mine = [b for b in beads if record.mission_id in str(b.get("title", ""))]
    # 3 tasks + 1 recovery attempt = 4 real work items, each completed.
    assert len(mine) == 4, [b.get("title") for b in beads]
    assert all(str(b.get("status")) == "closed" for b in mine)

    jobs = tmp_path / "state" / "gas-city-jobs" / record.mission_id
    job_ids = []
    for task in record.tasks:
        for attempt in task.attempts:
            envelope = json.loads(
                (jobs / task.task_id / f"attempt-{attempt.attempt}" / "request.json").read_text()
            )
            provenance = json.loads(
                (jobs / task.task_id / f"attempt-{attempt.attempt}" / "transport.json").read_text()
            )
            job_ids.append(envelope["gas_city"]["bead_id"])
            assert envelope["gas_city"]["city"] == str(city)
            assert provenance["terminal_state"] == "completed"
    # One job per attempt, all distinct.
    assert len(job_ids) == len(set(job_ids)) == 4


def test_gate_b_routing_selected_three_specialists(gate_b: dict[str, Any]) -> None:
    record, model = gate_b["record"], gate_b["model"]
    routed = {i["task_id"]: i["specialist_id"] for i in record.routing}
    assert routed == {
        "arith-code": "python_backend",
        "arith-tests": "test_engineer",
        "arith-docs": "technical_writer",
    }
    # Each routed profile reached a real out-of-process worker.
    joined = " ".join(model.implementer_systems)
    assert "backend implementation specialist" in joined
    assert "test engineering specialist" in joined
    assert "documentation specialist" in joined


def test_gate_b_recovery_was_bounded_and_doom_team_owned(gate_b: dict[str, Any]) -> None:
    record, tmp_path = gate_b["record"], gate_b["tmp_path"]
    tests_task = record.task("arith-tests")
    assert [a.status for a in tests_task.attempts] == ["qa_blocked", "accepted"]
    assert record.max_attempts_per_task == 2
    jobs = tmp_path / "state" / "gas-city-jobs" / record.mission_id / "arith-tests"
    first = json.loads((jobs / "attempt-1" / "request.json").read_text())
    second = json.loads((jobs / "attempt-2" / "request.json").read_text())
    # A NEW Gas City job, told what went wrong, on the same pinned frontier.
    assert first["gas_city"]["bead_id"] != second["gas_city"]["bead_id"]
    assert second["remediation_summary"]
    assert first["baseline_sha"] == second["baseline_sha"]
    # The other tasks needed exactly one attempt each.
    assert len(record.task("arith-code").attempts) == 1
    assert len(record.task("arith-docs").attempts) == 1


def test_gate_b_deliverable_holds_the_work_and_nothing_was_published(
    gate_b: dict[str, Any],
) -> None:
    repo = DeliverableRepo.open(gate_b["tmp_path"] / "project")
    for name in ("arith.py", "test_arith.py", "test_arith_edge.py", "README.md"):
        assert (repo.path / name).exists(), name
    assert repo.remotes() == ()
    assert repo.head() == gate_b["record"].final_frontier_sha


def test_gate_b_no_autopilot_was_involved(gate_b: dict[str, Any]) -> None:
    """Standalone means standalone: no Autopilot process, import or state."""
    tmp_path = gate_b["tmp_path"]
    blob = json.dumps(gate_b["record"].to_dict())
    assert "doom_autopilot" not in blob and "autopilot" not in blob.lower()
    for path in (tmp_path / "state").rglob("*.json"):
        text = path.read_text()
        assert "doom_autopilot" not in text
        assert "/runtime/cities/doom-team/" not in text  # the Autopilot city
        assert "/runtime/contracts/" not in text
    # The Doom Team city is its own, not an Autopilot one.
    assert "autopilot" not in str(gate_b["city"]).lower()
    import sys as _sys

    assert not any(m.startswith("doom_autopilot") for m in _sys.modules)


def test_gate_b_explicit_approval_completes_the_mission(
    gate_b: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    tmp_path, mission_id = gate_b["tmp_path"], gate_b["mission_id"]
    previous = {k: os.environ.get(k) for k in gate_b["env"]}
    os.environ.update(gate_b["env"])
    try:
        store = MissionStore(tmp_path / "state")
        if store.load(mission_id).status == MISSION_AWAITING_HUMAN_APPROVAL:
            rc = main(
                ["approve", "--mission-id", mission_id, "--operator", "eko",
                 "--decision", "approve"]
            )
            assert rc == 0
            capsys.readouterr()
        record = MissionStore(tmp_path / "state").load(mission_id)
        assert record.status == MISSION_APPROVED
        assert record.approval is not None and record.approval["operator"] == "eko"
        # Approval decided; it published nothing.
        assert DeliverableRepo.open(tmp_path / "project").remotes() == ()
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def test_gate_b_secrets_never_reached_durable_state(gate_b: dict[str, Any]) -> None:
    tmp_path = gate_b["tmp_path"]
    for path in (tmp_path / "state").rglob("*"):
        if path.is_file():
            try:
                text = path.read_text()
            except (UnicodeDecodeError, OSError):
                continue
            assert API_KEY not in text, path
    assert API_KEY not in json.dumps(_beads(gate_b["city"]))


# --- T8-I: process loss and explicit resume, over real Gas City -----------


def test_resume_after_process_loss_over_gas_city(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A new process picks the mission up from durable state and finishes it."""
    model = GateBModel(
        {
            CODE: [{"files": GOOD_CODE}],
            TESTS: [{"files": EDGE_TESTS}],
            DOCS: [{"files": DOCS_FILES}],
        }
    )
    server, base_url = _serve(model)
    city = tmp_path / "city"
    try:
        _apply(monkeypatch, _gas_city_env(tmp_path, base_url, city))

        # Interrupt the first process after the FIRST task integrates.
        import doom_team.mission_runner as runner_module

        real_integrate = runner_module.MissionRunner._integrate

        class Interrupted(Exception):
            pass

        def stop_after_first(self, mission_id, task, repo, task_record):  # type: ignore[no-untyped-def]
            outcome = real_integrate(self, mission_id, task, repo, task_record)
            if task.id == "arith-code":
                raise Interrupted("process lost")
            return outcome

        monkeypatch.setattr(runner_module.MissionRunner, "_integrate", stop_after_first)
        with pytest.raises(Interrupted):
            main(["run", "Build a tiny arithmetic package", "--destination", str(tmp_path / "project")])
        monkeypatch.setattr(runner_module.MissionRunner, "_integrate", real_integrate)

        store = MissionStore(tmp_path / "state")
        mission_id = store.list_ids()[0]
        before = store.load(mission_id)
        assert before.task("arith-code").integrated
        assert before.task("arith-tests").state == "pending"
        implemented_before = len(model.implementer_systems)

        # A brand-new process resumes through the same real Gas City runtime.
        rc = main(["resume", mission_id])
        assert rc == 0
        output = json.loads(capsys.readouterr().out)
        assert output["task_transport"] == TRANSPORT_GAS_CITY
    finally:
        server.shutdown()

    after = MissionStore(tmp_path / "state").load(mission_id)
    assert after.status == MISSION_AWAITING_HUMAN_APPROVAL
    assert all(t.integrated for t in after.tasks)
    # The completed task was NOT re-executed, and its budget was not refilled.
    assert len(after.task("arith-code").attempts) == 1
    assert after.task("arith-code").integrated_sha == before.task("arith-code").integrated_sha
    assert len(model.implementer_systems) == implemented_before + 2  # only the remaining two
    assert after.final_validation is not None and after.final_validation_passed


# --- T8-H: exhaustion over the product path -------------------------------


def test_exhaustion_over_gas_city_blocks_the_mission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    model = GateBModel(
        {
            CODE: [{"files": GOOD_CODE}],
            # Always blocked by QA: the bound must stop it.
            TESTS: [{"files": EDGE_TESTS, "qa": BLOCK_VERDICT}],
            DOCS: [{"files": DOCS_FILES}],
        }
    )
    server, base_url = _serve(model)
    city = tmp_path / "city"
    try:
        _apply(monkeypatch, _gas_city_env(tmp_path, base_url, city))
        rc = main(["run", "Build it", "--destination", str(tmp_path / "project")])
        assert rc == 4
        output = json.loads(capsys.readouterr().out)
    finally:
        server.shutdown()

    assert output["status"] == "blocked"
    assert output["blocker"]["reason"] == "recovery_exhausted"
    record = MissionStore(tmp_path / "state").load(output["mission_id"])
    tests_task = record.task("arith-tests")
    assert len(tests_task.attempts) == record.max_attempts_per_task == 2
    # Exactly the configured number of Gas City jobs - Gas City never resubmits.
    jobs = tmp_path / "state" / "gas-city-jobs" / record.mission_id / "arith-tests"
    assert sorted(p.name for p in jobs.iterdir()) == ["attempt-1", "attempt-2"]
    # The dependent task never ran, and the gate was never reached.
    assert record.task("arith-docs").state == "pending"
    assert not (tmp_path / "state" / "gas-city-jobs" / record.mission_id / "arith-docs").exists()
    assert record.final_validation is None
    assert record.approval is None


# --- T8-Y: two missions through one runtime, no contamination -------------


def test_two_sequential_missions_share_a_runtime_safely(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    city = tmp_path / "shared-city"
    missions: list[str] = []
    for index in (1, 2):
        model = GateBModel(
            {
                CODE: [{"files": GOOD_CODE}],
                TESTS: [{"files": EDGE_TESTS}],
                DOCS: [{"files": DOCS_FILES}],
            }
        )
        server, base_url = _serve(model)
        try:
            _apply(monkeypatch, _gas_city_env(tmp_path, base_url, city))
            rc = main(
                ["run", f"Build package {index}", "--destination", str(tmp_path / f"project{index}")]
            )
            assert rc == 0, f"mission {index} did not complete"
            missions.append(json.loads(capsys.readouterr().out)["mission_id"])
        finally:
            server.shutdown()

    assert len(set(missions)) == 2
    store = MissionStore(tmp_path / "state")
    for mission_id in missions:
        record = store.load(mission_id)
        assert record.status == MISSION_AWAITING_HUMAN_APPROVAL
        assert all(t.integrated for t in record.tasks)

    # Each mission's work items carry only its own identity.
    beads = _beads(city)
    for mission_id in missions:
        mine = [b for b in beads if mission_id in str(b.get("title", ""))]
        assert len(mine) == 3, [b.get("title") for b in beads]
        assert all(str(b.get("status")) == "closed" for b in mine)
    # No job directory from one mission appears under the other.
    jobs_root = tmp_path / "state" / "gas-city-jobs"
    assert sorted(p.name for p in jobs_root.iterdir()) == sorted(missions)
    # Distinct deliverables, distinct frontiers.
    first, second = (store.load(m) for m in missions)
    assert first.final_frontier_sha != second.final_frontier_sha
    assert first.deliverable_path != second.deliverable_path


# --- Integrity-review regressions -----------------------------------------


def test_resume_refuses_to_switch_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A Gas City mission is not quietly finished by a local in-process runtime."""
    model = GateBModel(
        {CODE: [{"files": GOOD_CODE}], TESTS: [{"files": EDGE_TESTS}], DOCS: [{"files": DOCS_FILES}]}
    )
    server, base_url = _serve(model)
    city = tmp_path / "city"
    try:
        _apply(monkeypatch, _gas_city_env(tmp_path, base_url, city))
        import doom_team.mission_runner as runner_module

        real_integrate = runner_module.MissionRunner._integrate

        class Interrupted(Exception):
            pass

        def stop_after_first(self, mission_id, task, repo, task_record):  # type: ignore[no-untyped-def]
            outcome = real_integrate(self, mission_id, task, repo, task_record)
            if task.id == "arith-code":
                raise Interrupted("process lost")
            return outcome

        monkeypatch.setattr(runner_module.MissionRunner, "_integrate", stop_after_first)
        with pytest.raises(Interrupted):
            main(["run", "Build it", "--destination", str(tmp_path / "project")])
        monkeypatch.setattr(runner_module.MissionRunner, "_integrate", real_integrate)

        store = MissionStore(tmp_path / "state")
        mission_id = store.list_ids()[0]
        assert store.load(mission_id).task_transport == TRANSPORT_GAS_CITY
        implemented = len(model.implementer_systems)

        # The operator resumes from a shell that forgot the transport config.
        for key in ("DOOM_TASK_TRANSPORT", "DOOM_GAS_CITY_BINARY", "DOOM_GAS_CITY_CITY"):
            monkeypatch.delenv(key, raising=False)
        rc = main(["resume", mission_id])
        assert rc == 4
        output = json.loads(capsys.readouterr().out)
    finally:
        server.shutdown()

    assert output["blocker"]["reason"] == "transport_mismatch"
    # Nothing was executed locally behind the operator's back.
    assert len(model.implementer_systems) == implemented
    after = MissionStore(tmp_path / "state").load(mission_id)
    assert after.task("arith-tests").state == "pending"


def test_broken_city_paths_fail_closed_without_a_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Unusable city paths report cleanly instead of crashing the command."""
    a_file = tmp_path / "afile"
    a_file.write_text("not a city")
    locked_parent = tmp_path / "locked"
    locked_parent.mkdir()
    locked_parent.chmod(0o500)
    try:
        for city in (a_file, locked_parent / "city"):
            _apply(monkeypatch, _gas_city_env(tmp_path, "http://127.0.0.1:1", city))
            rc = main(["run", "Build it", "--destination", str(tmp_path / "p")])
            assert rc == 1
            assert "task transport unavailable" in capsys.readouterr().err
    finally:
        locked_parent.chmod(0o700)


def test_credential_variable_must_be_one_the_guard_strips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A credential parked in an unrecognized variable would reach untrusted code."""
    monkeypatch.setenv("DOOM_PROVIDER_CRED", "canary")
    with pytest.raises(ConfigError, match="would not be stripped"):
        ConfigLoader().load(
            state_dir=str(tmp_path / "state"), model_provider="nvidia", model_name="m",
            provider_base_url="http://127.0.0.1:1", provider_api_key_env="DOOM_PROVIDER_CRED",
        )
    # A recognized name is accepted.
    monkeypatch.setenv("DOOM_OTHER_API_KEY", "canary")
    settings = ConfigLoader().load(
        state_dir=str(tmp_path / "state"), model_provider="nvidia", model_name="m",
        provider_base_url="http://127.0.0.1:1", provider_api_key_env="DOOM_OTHER_API_KEY",
    )
    assert settings.provider_api_key_env == "DOOM_OTHER_API_KEY"


def test_planted_rootdir_cannot_forge_the_validation_verdict(tmp_path: Path) -> None:
    """A pytest.ini + conftest in a parent directory must not rewrite the exit code."""
    from doom_team.transport import VALIDATION_POLICIES
    from doom_team.validation import run_validation_commands

    shared = tmp_path / "shared"
    export = shared / "export"
    export.mkdir(parents=True)
    (shared / "pytest.ini").write_text("[pytest]\n")
    (shared / "conftest.py").write_text(
        "def pytest_sessionfinish(session, exitstatus):\n    session.exitstatus = 0\n"
    )
    (export / "test_broken.py").write_text("def test_broken():\n    assert 1 == 2\n")

    results = run_validation_commands(export, VALIDATION_POLICIES["default"])
    tests = [r for r in results if r.label == "tests"][0]
    assert not tests.passed, "a planted parent rootdir forged a passing verdict"

    # A genuinely good tree still passes.
    (export / "test_broken.py").write_text("def test_ok():\n    assert True\n")
    good = run_validation_commands(export, VALIDATION_POLICIES["default"])
    assert all(r.passed for r in good)


def test_the_deliverable_repository_is_hardened_like_a_workspace(tmp_path: Path) -> None:
    """The artifact a human approves must not be able to hide its own diff."""
    repo = DeliverableRepo.create(tmp_path / "project")
    pin = repo.path / ".git" / "info" / "attributes"
    assert pin.exists() and "-diff" not in pin.read_text().split("*", 1)[1].split()[0]
    assert "diff" in pin.read_text()
    # Opening an older repository asserts the pin too.
    pin.unlink()
    DeliverableRepo.open(tmp_path / "project")
    assert pin.exists()


def test_foreign_work_items_do_not_consume_mission_attempts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Items routed by anything else are drained, not charged to the mission."""
    city = tmp_path / "city"
    ensure_gas_city(city, GC_BINARY)
    env = dict(os.environ, PATH=f"{GC_TOOLS}:{os.environ.get('PATH', '')}")
    for index in range(3):
        subprocess.run(
            [str(GC_BINARY), "sling", "--city", str(city), "--json", "--no-convoy",
             "mayor", f"foreign item {index}"],
            cwd=str(city), capture_output=True, text=True, timeout=180, env=env,
        )

    model = GateBModel(
        {CODE: [{"files": GOOD_CODE}], TESTS: [{"files": EDGE_TESTS}], DOCS: [{"files": DOCS_FILES}]}
    )
    server, base_url = _serve(model)
    try:
        _apply(monkeypatch, _gas_city_env(tmp_path, base_url, city))
        rc = main(["run", "Build it", "--destination", str(tmp_path / "project")])
        assert rc == 0, capsys.readouterr().err
        output = json.loads(capsys.readouterr().out)
    finally:
        server.shutdown()

    assert output["status"] == MISSION_AWAITING_HUMAN_APPROVAL
    record = MissionStore(tmp_path / "state").load(output["mission_id"])
    # Every task still needed exactly one attempt: the foreign items cost none.
    assert [len(t.attempts) for t in record.tasks] == [1, 1, 1]


def test_doctor_reports_an_unusable_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Readiness covers where work would run, not just state and credentials."""
    broken = tmp_path / "not-a-city"
    broken.mkdir()
    (broken / "occupied.txt").write_text("x")
    _apply(monkeypatch, _gas_city_env(tmp_path, "http://127.0.0.1:1", broken))
    assert main(["doctor"]) == 2
    assert "task transport unusable" in capsys.readouterr().err

    # A usable configuration reports the transport and passes.
    _apply(monkeypatch, _gas_city_env(tmp_path, "http://127.0.0.1:1", tmp_path / "good-city"))
    assert main(["doctor"]) == 0
    assert "task_transport gas_city usable" in capsys.readouterr().out

    assert main(["status"]) == 0
    assert "task_transport: gas_city" in capsys.readouterr().out
