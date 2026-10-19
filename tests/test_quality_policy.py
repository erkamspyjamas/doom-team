"""Trusted quality gates execute before review and final approval, not after it."""
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from doom_team.cli import _build_mission_runner
from doom_team.config import ConfigError, ConfigLoader, Settings
from doom_team.deliverable import DeliverableRepo
from doom_team.domain import TaskContract
from doom_team.mission import MissionRecord, MissionStore
from doom_team.mission_runner import MissionRunner
from doom_team.routing import route_task
from doom_team.transport import (
    TaskAttemptRequest, TransportError, resolve_validation_policy, validation_fingerprint,
)
from doom_team.validation import run_validation_commands
from doom_team import worker


@pytest.fixture
def quality_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    tools = tmp_path / "tools"
    tools.mkdir()
    for name in ("ruff", "mypy"):
        executable = tools / name
        executable.write_text(f"#!{sys.executable}\nimport sys\nprint('executed {name}', *sys.argv[1:])\n")
        executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(tools) + os.pathsep + os.environ.get("PATH", ""))
    return tools


def settings(tmp_path: Path, **kw: object) -> Settings:
    return Settings(state_dir=tmp_path / "state", model_provider="openrouter", model_name="fake",
                    validation_policy="python-quality", python_typecheck_target="sample", **kw)


@pytest.mark.parametrize("target", ["../outside", "/tmp/pkg", "--help", "x;echo", "x y", "a/../b", "a\\b", ""])
def test_config_rejects_unsafe_targets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str) -> None:
    monkeypatch.setenv("DOOM_API_KEY", "test-only-placeholder")
    with pytest.raises(ConfigError, match="safe relative source path"):
        ConfigLoader().load(state_dir=str(tmp_path), model_provider="openrouter", model_name="fake",
                            validation_policy="python-quality", python_typecheck_target=target)


def test_config_env_and_explicit_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOOM_API_KEY", "test-only-placeholder")
    monkeypatch.setenv("DOOM_VALIDATION_POLICY", "python-quality")
    monkeypatch.setenv("DOOM_PYTHON_TYPECHECK_TARGET", "sample")
    loaded = ConfigLoader().load(state_dir=str(tmp_path), model_provider="openrouter", model_name="fake")
    assert loaded.validation_policy == "python-quality" and loaded.python_typecheck_target == "sample"
    assert loaded.as_evidence_dict()["validation_policy"] == "python-quality"
    explicit = ConfigLoader().load(state_dir=str(tmp_path), model_provider="openrouter", model_name="fake",
                                   validation_policy="default", python_typecheck_target=".")
    assert explicit.validation_policy == "default" and explicit.python_typecheck_target == "."
    with pytest.raises(ConfigError, match="validation_policy"):
        ConfigLoader().load(state_dir=str(tmp_path), model_provider="openrouter", model_name="fake",
                            validation_policy="invented")


def test_default_policy_remains_compile_and_pytest() -> None:
    assert [c.label for c in resolve_validation_policy("default")] == ["compile", "tests"]


def test_missing_quality_tool_fails_before_planning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(TransportError, match="requires executable ruff and mypy"):
        resolve_validation_policy("python-quality", typecheck_target="sample")
    with pytest.raises(ConfigError, match="requires executable ruff and mypy"):
        _build_mission_runner(settings(tmp_path), SimpleNamespace())


def test_quality_argv_is_confined_and_pinned(quality_path: Path) -> None:
    commands = resolve_validation_policy("python-quality", typecheck_target="sample")
    assert [c.label for c in commands] == ["compile", "tests", "ruff", "mypy"]
    assert commands[-2].argv == (str(quality_path / "ruff"), "check", "--isolated", "--select", "E4,E7,E9,F", ".")
    assert commands[-1].argv == (str(quality_path / "mypy"), "--strict", "--no-incremental", "sample")
    with pytest.raises(TransportError, match="safe relative source path"):
        resolve_validation_policy("python-quality", typecheck_target="../escape")


FILES = {"sample/__init__.py": "def add(a: int, b: int) -> int:\n    return a + b\n",
         "test_sample.py": "from sample import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"}


class GateAwareInference:
    def __init__(self, plan: bool = True) -> None:
        self.plan = plan
        self.calls: list[list[dict[str, str]]] = []

    def complete(self, messages: list[dict[str, str]]) -> SimpleNamespace:
        self.calls.append(messages)
        step = len(self.calls) - (1 if self.plan else 0)
        if step == 0:
            content = {"tasks": [{"task_id": "implement", "description": "Implement sample and passing tests",
                        "dependencies": [], "required_capabilities": ["backend.python"],
                        "acceptance_criteria": ["All four validation gates pass"]}]}
        elif step == 1:
            content = {"files": [{"path": path, "content": value} for path, value in FILES.items()],
                       "summary": "Implement typed add and regression test"}
        else:
            evidence = messages[1]["content"]
            for label in ("compile", "tests", "ruff", "mypy"):
                assert f'"label": "{label}"' in evidence
            assert "executed ruff" in evidence and "executed mypy" in evidence
            content = {"verdict": "pass", "summary": "All executed gates passed", "blockers": []}
        return SimpleNamespace(content=json.dumps(content))


def test_cli_task_and_final_gates_include_quality_before_review(tmp_path: Path, quality_path: Path) -> None:
    client = GateAwareInference()
    config = settings(tmp_path)
    runner = _build_mission_runner(config, client)
    result = runner.run("Implement sample with tests", tmp_path / "project")
    record = MissionStore(config.state_dir).load(result.mission_id)
    assert record.status == "awaiting_human_approval"
    assert record.validation_fingerprint
    assert len(client.calls) == 4
    assert [v["label"] for v in record.tasks[0].validation] == ["compile", "tests", "ruff", "mypy"]
    assert record.final_validation
    assert [v["label"] for v in record.final_validation] == ["compile", "tests", "ruff", "mypy"]
    assert record.final_frontier_sha == record.final_validation_sha
    assert record.approval is None
    assert MissionRecord.from_dict(record.to_dict()).validation_fingerprint == record.validation_fingerprint


def make_request(tmp_path: Path, fingerprint: str) -> TaskAttemptRequest:
    task = TaskContract(id="implement", objective_id="objective", instruction="Implement sample and tests",
                        acceptance_criteria=("All gates pass",), required_capabilities=("backend.python",))
    repo = DeliverableRepo.create(tmp_path / "project")
    workspace = repo.create_task_workspace(task.id, tmp_path / "workspaces")
    return TaskAttemptRequest(mission_id="mission-abcdef123456-1234abcd", task_id=task.id, attempt=1,
                             baseline_sha=workspace.baseline_sha, workspace_dir=workspace.path.name,
                             branch=workspace.branch, workspaces_root=str(tmp_path / "workspaces"), task=task,
                             decision=route_task(task), validation_policy="python-quality",
                             validation_fingerprint=fingerprint)


def test_worker_executes_same_quality_gates(
    tmp_path: Path, quality_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands = resolve_validation_policy("python-quality", typecheck_target="sample")
    request = make_request(tmp_path, validation_fingerprint("python-quality", commands))
    config = settings(tmp_path)
    monkeypatch.setattr(worker.ConfigLoader, "load", lambda self: config)
    client = GateAwareInference(plan=False)
    monkeypatch.setattr(worker, "build_inference_client", lambda *a, **kw: client)
    result = worker.run_attempt(TaskAttemptRequest.from_dict(request.to_dict()))
    assert result["status"] == "accepted"
    assert [v["label"] for v in result["validation_results"]] == ["compile", "tests", "ruff", "mypy"]
    assert len(client.calls) == 3


def test_worker_rejects_drifted_target_before_model_call(tmp_path: Path, quality_path: Path,
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    commands = resolve_validation_policy("python-quality", typecheck_target="different")
    request = make_request(tmp_path, validation_fingerprint("python-quality", commands))
    config = settings(tmp_path)
    monkeypatch.setattr(worker.ConfigLoader, "load", lambda self: config)
    client = GateAwareInference(plan=False)
    monkeypatch.setattr(worker, "build_inference_client", lambda *a, **kw: client)
    with pytest.raises(TransportError, match="differs from the pinned request"):
        worker.run_attempt(request)
    assert not client.calls


@pytest.mark.parametrize("quality_record", [False, True])
def test_resume_rejects_policy_drift_without_new_attempt(
    tmp_path: Path, quality_path: Path, quality_record: bool,
) -> None:
    repo = DeliverableRepo.create(tmp_path / "project")
    store = MissionStore(tmp_path / "state")
    original = resolve_validation_policy("python-quality", typecheck_target="sample")
    fingerprint = validation_fingerprint("python-quality", original) if quality_record else ""
    record = store.create(mission_id="mission-abcdef123456-1234abcd", objective="Build it", objective_id="objective",
                          plan_mission_id="plan", task_transport="in_process", validation_fingerprint=fingerprint)
    store.record_deliverable(record.mission_id, deliverable_path=str(repo.path), baseline_sha=repo.head())
    client = GateAwareInference()
    changed = resolve_validation_policy("python-quality", typecheck_target="changed")
    runner = MissionRunner(client, store, workspaces_root=tmp_path / "workspaces",
                           validation_policy="python-quality", validation_commands=changed)
    result = runner.resume(record.mission_id)
    assert result.status == "blocked"
    assert store.load(record.mission_id).blocker["reason"] == "validation_policy_mismatch"
    assert not client.calls
    if not quality_record:
        assert "validation_fingerprint" not in record.to_dict()


@pytest.mark.parametrize("failed_tool", ["ruff", "mypy"])
def test_failed_quality_exit_is_a_real_failed_gate(tmp_path: Path, quality_path: Path, failed_tool: str) -> None:
    (tmp_path / "sample.py").write_text("x = 1\n")
    executable = quality_path / failed_tool
    executable.write_text(f"#!{sys.executable}\nimport sys\nprint('quality failure')\nsys.exit(1)\n")
    commands = resolve_validation_policy("python-quality", typecheck_target="sample.py")[-2:]
    results = run_validation_commands(tmp_path, commands)
    assert next(v for v in results if v.label == failed_tool).command.exit_code == 1
    assert not all(v.passed for v in results)


def test_resume_refuses_quality_downgrade(tmp_path: Path, quality_path: Path) -> None:
    repo = DeliverableRepo.create(tmp_path / "project")
    store = MissionStore(tmp_path / "state")
    commands = resolve_validation_policy("python-quality", typecheck_target="sample")
    record = store.create(
        mission_id="mission-abcdef123456-1234abcd", objective="Build it", objective_id="objective",
        plan_mission_id="plan", task_transport="in_process",
        validation_fingerprint=validation_fingerprint("python-quality", commands),
    )
    store.record_deliverable(record.mission_id, deliverable_path=str(repo.path), baseline_sha=repo.head())
    client = GateAwareInference()
    runner = MissionRunner(client, store, workspaces_root=tmp_path / "workspaces")
    assert runner.resume(record.mission_id).status == "blocked"
    assert not client.calls
