"""Tests for the doom-team CLI subcommands."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from doom_team.approval import DurableApprovalPort
from doom_team.cli import main


@pytest.fixture
def cli_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Set up a valid environment for CLI commands."""
    monkeypatch.setenv("DOOM_API_KEY", "secret")
    monkeypatch.setenv("DOOM_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("DOOM_MODEL_PROVIDER", "nvidia")
    monkeypatch.setenv("DOOM_MODEL_NAME", "deepseek-v4")
    return dict(os.environ)


def test_doctor_healthy(tmp_path: Path, cli_env: dict[str, str]) -> None:
    rc = main(["doctor"])
    assert rc == 0


def test_doctor_config_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DOOM_API_KEY", raising=False)
    monkeypatch.setenv("DOOM_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("DOOM_MODEL_PROVIDER", "nvidia")
    monkeypatch.setenv("DOOM_MODEL_NAME", "deepseek-v4")
    rc = main(["doctor"])
    assert rc == 1


def test_status_reports_state(tmp_path: Path, cli_env: dict[str, str]) -> None:
    from doom_team.evidence import DurableEvidenceStore
    from doom_team.verdicts import DurableVerdictStore, Verdict

    state_dir = Path(os.environ["DOOM_STATE_DIR"])
    ev_store = DurableEvidenceStore(state_dir)
    ev_store.save(
        {
            "id": "ev-1",
            "candidate_id": "cand-1",
            "candidate_commit": "abc",
            "objective_id": "obj-1",
            "mission_id": "mission-1",
            "producer_role": "dev",
            "producer_run": "run-1",
            "command": "make test",
            "artifacts": [],
            "actual_model": "deepseek-v4",
            "actual_runtime": "local",
            "timestamp": "2024-01-01T00:00:00+00:00",
            "digest": "sha256:abc",
        }
    )
    verdict_store = DurableVerdictStore(state_dir)
    verdict_store.save(Verdict(candidate_id="cand-1", role="QA", decision=True))

    rc = main(["status"])
    assert rc == 0


def test_approve_records_approval(tmp_path: Path, cli_env: dict[str, str]) -> None:
    state_dir = Path(os.environ["DOOM_STATE_DIR"])
    rc = main(
        [
            "approve",
            "--action",
            "release",
            "--frontier-sha",
            "abc123",
            "--operator",
            "ops-1",
            "--decision",
            "approve",
        ]
    )
    assert rc == 0
    port = DurableApprovalPort(state_dir)
    assert port.request_approval("release", {"frontier_sha": "abc123"}) is True


def test_approve_deny_never_authorizes(tmp_path: Path, cli_env: dict[str, str]) -> None:
    state_dir = Path(os.environ["DOOM_STATE_DIR"])
    rc = main(
        [
            "approve",
            "--action",
            "release",
            "--frontier-sha",
            "abc123",
            "--operator",
            "ops-1",
            "--decision",
            "deny",
        ]
    )
    assert rc == 0
    port = DurableApprovalPort(state_dir)
    assert port.request_approval("release", {"frontier_sha": "abc123"}) is False


def test_approve_true_false_spellings(tmp_path: Path, cli_env: dict[str, str]) -> None:
    state_dir = Path(os.environ["DOOM_STATE_DIR"])
    rc = main(
        [
            "approve",
            "--action",
            "release",
            "--frontier-sha",
            "sha1",
            "--operator",
            "ops-1",
            "--decision",
            "true",
        ]
    )
    assert rc == 0
    port = DurableApprovalPort(state_dir)
    assert port.request_approval("release", {"frontier_sha": "sha1"}) is True

    rc = main(
        [
            "approve",
            "--action",
            "release",
            "--frontier-sha",
            "sha2",
            "--operator",
            "ops-1",
            "--decision",
            "false",
        ]
    )
    assert rc == 0
    assert port.request_approval("release", {"frontier_sha": "sha2"}) is False


def test_approve_invalid_decision_fails_closed(tmp_path: Path, cli_env: dict[str, str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(
            [
                "approve",
                "--action",
                "release",
                "--frontier-sha",
                "abc123",
                "--operator",
                "ops-1",
                "--decision",
                "maybe",
            ]
        )
    assert excinfo.value.code == 2
    state_dir = Path(os.environ["DOOM_STATE_DIR"])
    assert list((state_dir / "approvals").glob("*.json")) == []


def test_approve_missing_decision_fails_closed(tmp_path: Path, cli_env: dict[str, str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(
            [
                "approve",
                "--action",
                "release",
                "--frontier-sha",
                "abc123",
                "--operator",
                "ops-1",
            ]
        )
    assert excinfo.value.code == 2
    state_dir = Path(os.environ["DOOM_STATE_DIR"])
    assert list((state_dir / "approvals").glob("*.json")) == []


def test_approve_stale_frontier_fails_closed(tmp_path: Path, cli_env: dict[str, str]) -> None:
    state_dir = Path(os.environ["DOOM_STATE_DIR"])
    port = DurableApprovalPort(state_dir, contract_revision="contract-1")
    port.approve("release", "abc123", "ops-1", decision=True, contract_revision="contract-0")
    assert port.request_approval("release", {"frontier_sha": "abc123"}) is False


def test_approve_mismatched_frontier_fails_closed(tmp_path: Path, cli_env: dict[str, str]) -> None:
    state_dir = Path(os.environ["DOOM_STATE_DIR"])
    rc = main(
        [
            "approve",
            "--action",
            "release",
            "--frontier-sha",
            "abc123",
            "--operator",
            "ops-1",
            "--decision",
            "approve",
        ]
    )
    assert rc == 0
    port = DurableApprovalPort(state_dir)
    assert port.request_approval("release", {"frontier_sha": "other"}) is False


def test_approve_missing_required_args(tmp_path: Path, cli_env: dict[str, str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["approve", "--action", "release"])
    assert excinfo.value.code == 2  # argparse error exit


def test_backup_documented_command(tmp_path: Path, cli_env: dict[str, str]) -> None:
    from doom_team.evidence import DurableEvidenceStore

    state_dir = Path(os.environ["DOOM_STATE_DIR"])
    DurableEvidenceStore(state_dir)
    backup_dir = tmp_path / "backups"
    rc = main(["backup", "--state-dir", str(state_dir), "--backup-dir", str(backup_dir)])
    assert rc == 0
    assert any(backup_dir.iterdir())


def test_restore_documented_command(tmp_path: Path, cli_env: dict[str, str]) -> None:
    from doom_team.evidence import DurableEvidenceStore

    state_dir = Path(os.environ["DOOM_STATE_DIR"])
    DurableEvidenceStore(state_dir)
    backup_dir = tmp_path / "backups"
    rc = main(["backup", "--state-dir", str(state_dir), "--backup-dir", str(backup_dir)])
    assert rc == 0
    backup_path = next(backup_dir.iterdir())
    rc = main(["restore", "--state-dir", str(state_dir), "--backup-path", str(backup_path)])
    assert rc == 0
    assert (state_dir / "evidence").is_dir()


def test_restore_corrupt_backup_fails_closed(tmp_path: Path, cli_env: dict[str, str]) -> None:
    state_dir = Path(os.environ["DOOM_STATE_DIR"])
    state_dir.mkdir(parents=True, exist_ok=True)
    bad_backup = tmp_path / "bad-backup"
    bad_backup.mkdir()
    (bad_backup / "random.txt").write_text("data")
    rc = main(["restore", "--state-dir", str(state_dir), "--backup-path", str(bad_backup)])
    assert rc == 2  # EXIT_STATE_ERROR


def test_serve_graceful_shutdown_and_restart_safe(tmp_path: Path, cli_env: dict[str, str]) -> None:
    """Serve starts, initializes state, and shuts down gracefully on SIGTERM."""
    env = dict(cli_env)
    proc = subprocess.Popen(
        [sys.executable, "-c", "from doom_team.cli import main; main()", "serve"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        # Wait for readiness
        assert proc.stdout is not None
        line = proc.stdout.readline().strip()
        assert line == "Doom Team service ready"
        # State dirs were created
        state_dir = Path(env["DOOM_STATE_DIR"])
        assert (state_dir / "evidence").is_dir()
        assert (state_dir / "verdicts").is_dir()
        # Graceful shutdown
        proc.send_signal(subprocess.signal.SIGTERM)
        proc.wait(timeout=5)
        assert proc.returncode == 0
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()

    # Restart-safe: second start should succeed and not error
    proc2 = subprocess.Popen(
        [sys.executable, "-c", "from doom_team.cli import main; main()", "serve"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        assert proc2.stdout is not None
        line = proc2.stdout.readline().strip()
        assert line == "Doom Team service ready"
        proc2.send_signal(subprocess.signal.SIGINT)
        proc2.wait(timeout=5)
        assert proc2.returncode == 0
    finally:
        if proc2.poll() is None:
            proc2.kill()
            proc2.wait()


# `doom-team run` plan-only vertical (T2): planner + routing wired into the CLI


class _PlanResponse:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakePlanClient:
    def __init__(self, content: str) -> None:
        self._content = content
        self.calls = 0

    def complete(self, messages):
        self.calls += 1
        return _PlanResponse(self._content)


def _plan_json() -> str:
    import json

    return json.dumps(
        {
            "tasks": [
                {
                    "task_id": "api-foundation",
                    "description": "Implement the REST API skeleton",
                    "dependencies": [],
                    "required_capabilities": ["backend.python"],
                    "acceptance_criteria": ["handlers exist"],
                },
                {
                    "task_id": "sqlite-storage",
                    "description": "Persist mappings in SQLite",
                    "dependencies": ["api-foundation"],
                    "required_capabilities": ["database.sqlite", "backend.python"],
                    "acceptance_criteria": ["mappings survive restart"],
                },
                {
                    "task_id": "automated-tests",
                    "description": "Add automated tests",
                    "dependencies": ["sqlite-storage"],
                    "required_capabilities": ["testing"],
                    "acceptance_criteria": ["pytest passes"],
                },
                {
                    "task_id": "readme-docs",
                    "description": "Write the README",
                    "dependencies": ["automated-tests"],
                    "required_capabilities": ["documentation"],
                    "acceptance_criteria": ["README documents install and run"],
                },
            ]
        }
    )


@pytest.fixture
def run_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    monkeypatch.setenv("DOOM_API_KEY", "run-secret")
    monkeypatch.setenv("DOOM_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("DOOM_MODEL_PROVIDER", "openrouter")
    monkeypatch.setenv("DOOM_MODEL_NAME", "planner-model")
    monkeypatch.setenv("DOOM_PROVIDER_BASE_URL", "https://router.example/api")
    return dict(os.environ)


def test_run_plan_only_routes_through_canonical_client_path(
    tmp_path: Path,
    run_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import json

    import doom_team.cli as cli_module

    fake = _FakePlanClient(_plan_json())
    captured_settings = {}

    def fake_factory(settings, **kwargs):
        captured_settings["settings"] = settings
        return fake

    monkeypatch.setattr(cli_module, "build_inference_client", fake_factory)
    rc = main(["run", "Build a small URL shortener REST API", "--plan-only"])
    assert rc == 0
    assert fake.calls == 1
    assert captured_settings["settings"].provider_base_url == "https://router.example/api"
    output = json.loads(capsys.readouterr().out)
    assert output["objective"]["description"] == "Build a small URL shortener REST API"
    assert len(output["tasks"]) == 4
    assert len(output["routing"]) == 4
    routed = {d["task_id"]: d["specialist_id"] for d in output["routing"]}
    assert routed["api-foundation"] == "python_backend"
    assert routed["sqlite-storage"] == "sqlite_persistence"
    assert routed["automated-tests"] == "test_engineer"
    assert routed["readme-docs"] == "technical_writer"
    # No secrets in the plan output.
    assert "run-secret" not in json.dumps(output)
    # Plan-only is ephemeral: no state or workspace mutation.
    assert not (tmp_path / "state").exists()


def test_run_without_plan_only_requires_a_destination(
    tmp_path: Path,
    run_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Execution builds a real repository, so the operator must say where."""
    import doom_team.cli as cli_module

    monkeypatch.setattr(
        cli_module,
        "build_inference_client",
        lambda settings, **kwargs: _FakePlanClient(_plan_json()),
    )
    rc = main(["run", "Build something"])
    assert rc == 4
    err = capsys.readouterr().err
    assert "--destination is required" in err
    # Nothing was created anywhere without an explicit destination.
    assert not (tmp_path / "state").exists()


def test_run_plan_only_malformed_plan_fails_closed(
    tmp_path: Path,
    run_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import doom_team.cli as cli_module

    monkeypatch.setattr(
        cli_module,
        "build_inference_client",
        lambda settings, **kwargs: _FakePlanClient("not json at all"),
    )
    rc = main(["run", "Build something", "--plan-only"])
    assert rc == 4
    assert "planning error" in capsys.readouterr().err


def test_run_plan_only_without_base_url_is_config_error(
    tmp_path: Path,
    run_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("DOOM_PROVIDER_BASE_URL")
    rc = main(["run", "Build something", "--plan-only"])
    assert rc == 1
    assert "provider_base_url" in capsys.readouterr().err


# `doom-team run` execution vertical (T5): the CLI drives the real orchestrator


def _mission_inference():
    from tests.test_mission_runner import MissionInference

    return MissionInference()


def test_run_executes_the_mission_runtime_from_objective_and_destination(
    tmp_path: Path,
    run_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Objective + destination is the whole contract: no task files, no roles."""
    import json

    import doom_team.cli as cli_module
    from doom_team.mission import MISSION_AWAITING_HUMAN_APPROVAL, MissionStore

    inference = _mission_inference()
    monkeypatch.setattr(
        cli_module, "build_inference_client", lambda settings, **kwargs: inference
    )
    destination = tmp_path / "delivered"
    rc = main(
        [
            "run",
            "Create a tiny Python arithmetic package",
            "--destination",
            str(destination),
        ]
    )
    assert rc == 0
    output = json.loads(capsys.readouterr().out)

    # Structured operator output.
    assert output["status"] == MISSION_AWAITING_HUMAN_APPROVAL
    assert output["mission_id"].startswith("mission-")
    assert output["approval_required"] is True
    assert output["frontier_sha"] and output["frontier_sha"] == output["final_frontier_sha"]
    assert output["deliverable_path"] == str(destination.resolve())
    assert len(output["tasks"]) == 3
    assert {t["state"] for t in output["tasks"]} == {"integrated"}
    assert len({r["specialist_id"] for r in output["routing"]}) == 3
    assert all(item["passed"] for item in output["final_validation"])
    assert "run-secret" not in json.dumps(output)

    # The mission really ran: a real deliverable exists at the frontier.
    assert (destination / "arith.py").exists()
    assert (destination / "README.md").exists()

    # Durable mission state is inspectable by mission id afterwards.
    record = MissionStore(tmp_path / "state").load(output["mission_id"])
    assert record.status == MISSION_AWAITING_HUMAN_APPROVAL
    assert "run-secret" not in (tmp_path / "state" / "missions").joinpath(
        f"{output['mission_id']}.json"
    ).read_text()


def test_run_blocked_mission_reports_nonzero_with_its_mission_id(
    tmp_path: Path,
    run_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import json

    import doom_team.cli as cli_module
    from tests.test_mission_runner import BLOCK_VERDICT, MissionInference

    monkeypatch.setattr(
        cli_module,
        "build_inference_client",
        lambda settings, **kwargs: MissionInference(qa=BLOCK_VERDICT),
    )
    rc = main(
        ["run", "Create a tiny Python arithmetic package", "--destination", str(tmp_path / "d")]
    )
    assert rc == 4
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "blocked"
    assert output["mission_id"]
    assert output["approval_required"] is False
    assert output["blocker"]["reason"] == "qa_blocked"


def test_approve_by_mission_id_completes_the_gate(
    tmp_path: Path,
    run_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import json

    import doom_team.cli as cli_module
    from doom_team.deliverable import DeliverableRepo
    from doom_team.mission import MISSION_APPROVED, MissionStore

    monkeypatch.setattr(
        cli_module, "build_inference_client", lambda settings, **kwargs: _mission_inference()
    )
    destination = tmp_path / "delivered"
    assert main(["run", "Build it", "--destination", str(destination)]) == 0
    mission_id = json.loads(capsys.readouterr().out)["mission_id"]

    rc = main(
        ["approve", "--mission-id", mission_id, "--operator", "eko", "--decision", "approve"]
    )
    assert rc == 0
    decided = json.loads(capsys.readouterr().out)
    assert decided["mission_id"] == mission_id
    assert decided["status"] == MISSION_APPROVED
    assert decided["approval"]["operator"] == "eko"

    assert MissionStore(tmp_path / "state").load(mission_id).status == MISSION_APPROVED
    # Approval decided; it never published anything.
    assert DeliverableRepo.open(destination).remotes() == ()


def test_approve_rejects_a_mission_that_is_not_awaiting_approval(
    tmp_path: Path,
    run_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import json

    import doom_team.cli as cli_module
    from tests.test_mission_runner import BLOCK_VERDICT, MissionInference

    monkeypatch.setattr(
        cli_module,
        "build_inference_client",
        lambda settings, **kwargs: MissionInference(qa=BLOCK_VERDICT),
    )
    assert main(["run", "Build it", "--destination", str(tmp_path / "d")]) == 4
    mission_id = json.loads(capsys.readouterr().out)["mission_id"]

    rc = main(
        ["approve", "--mission-id", mission_id, "--operator", "eko", "--decision", "approve"]
    )
    assert rc == 3
    assert "only a mission awaiting approval" in capsys.readouterr().err


def test_approve_unknown_mission_fails(
    tmp_path: Path, run_env: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    rc = main(
        [
            "approve",
            "--mission-id",
            "mission-abcdef123456-00112233",
            "--operator",
            "eko",
            "--decision",
            "approve",
        ]
    )
    assert rc == 3
    assert "unknown mission" in capsys.readouterr().err


def test_approve_without_mission_or_explicit_pair_fails(
    tmp_path: Path, run_env: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    rc = main(["approve", "--operator", "eko", "--decision", "approve"])
    assert rc == 3
    assert "provide --mission-id" in capsys.readouterr().err


def test_approve_rejects_conflicting_binding_flags(
    tmp_path: Path,
    run_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--mission-id derives the binding; naming a different one must not pass."""
    import json

    import doom_team.cli as cli_module
    from doom_team.mission import MISSION_AWAITING_HUMAN_APPROVAL, MissionStore

    monkeypatch.setattr(
        cli_module, "build_inference_client", lambda settings, **kwargs: _mission_inference()
    )
    assert main(["run", "Build it", "--destination", str(tmp_path / "d")]) == 0
    mission_id = json.loads(capsys.readouterr().out)["mission_id"]

    rc = main(
        [
            "approve",
            "--mission-id",
            mission_id,
            "--frontier-sha",
            "d" * 40,
            "--operator",
            "mallory",
            "--decision",
            "approve",
        ]
    )
    assert rc == 3
    assert "implied by --mission-id" in capsys.readouterr().err
    # The mission is untouched and still awaiting a real decision.
    assert MissionStore(tmp_path / "state").load(mission_id).status == (
        MISSION_AWAITING_HUMAN_APPROVAL
    )


def test_run_blocker_output_never_leaks_the_api_key(
    tmp_path: Path,
    run_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Printed mission state is the redacted state, not an in-memory original."""
    import doom_team.cli as cli_module

    monkeypatch.setattr(
        cli_module, "build_inference_client", lambda settings, **kwargs: _mission_inference()
    )
    # A destination whose own path carries the secret and is not empty, so the
    # failure detail quotes it back.
    destination = tmp_path / "run-secret"
    destination.mkdir()
    (destination / "occupied.txt").write_text("existing")
    rc = main(["run", "Build it", "--destination", str(destination)])
    assert rc == 4
    captured = capsys.readouterr()
    assert "run-secret" not in captured.out
    assert "[REDACTED]" in captured.out


def test_generic_approve_cannot_decide_a_mission_behind_its_back(
    tmp_path: Path,
    run_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The mission action namespace belongs to the ledger, not the generic path."""
    import json

    import doom_team.cli as cli_module
    from doom_team.mission import MISSION_AWAITING_HUMAN_APPROVAL, MissionStore

    monkeypatch.setattr(
        cli_module, "build_inference_client", lambda settings, **kwargs: _mission_inference()
    )
    assert main(["run", "Build it", "--destination", str(tmp_path / "d")]) == 0
    mission_id = json.loads(capsys.readouterr().out)["mission_id"]

    rc = main(
        [
            "approve",
            "--action",
            f"mission:{mission_id}",
            "--frontier-sha",
            "d" * 40,
            "--operator",
            "mallory",
            "--decision",
            "approve",
        ]
    )
    assert rc == 3
    assert "decided with --mission-id" in capsys.readouterr().err
    assert not list((tmp_path / "state" / "approvals").glob("*.json"))
    assert MissionStore(tmp_path / "state").load(mission_id).status == (
        MISSION_AWAITING_HUMAN_APPROVAL
    )


def test_approve_passes_operator_revisions_through_to_the_port(
    tmp_path: Path,
    run_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import json

    import doom_team.cli as cli_module
    from doom_team.approval import DurableApprovalPort

    monkeypatch.setattr(
        cli_module, "build_inference_client", lambda settings, **kwargs: _mission_inference()
    )
    assert main(["run", "Build it", "--destination", str(tmp_path / "d")]) == 0
    mission_id = json.loads(capsys.readouterr().out)["mission_id"]

    rc = main(
        [
            "approve",
            "--mission-id",
            mission_id,
            "--operator",
            "eko",
            "--decision",
            "approve",
            "--contract-revision",
            "contract-9",
            "--roadmap-revision",
            "roadmap-9",
        ]
    )
    assert rc == 0
    frontier = json.loads(capsys.readouterr().out)["frontier_sha"]
    record = DurableApprovalPort(tmp_path / "state").load(f"mission:{mission_id}", frontier)
    assert record.contract_revision == "contract-9"
    assert record.roadmap_revision == "roadmap-9"
    assert record.operator == "eko" and record.decision is True
