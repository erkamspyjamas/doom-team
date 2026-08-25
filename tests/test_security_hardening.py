"""Tests for practical subprocess safety controls."""

import shutil
from pathlib import Path

import pytest

from doom_team.subprocess_guard import is_sensitive_env_key, run_guarded, sanitize_env
from doom_team.workspace import GitWorkspace


def test_is_sensitive_env_key() -> None:
    assert is_sensitive_env_key("DOOM_API_KEY")
    assert is_sensitive_env_key("AWS_SECRET_ACCESS_KEY")
    assert is_sensitive_env_key("MY_TOKEN")
    assert not is_sensitive_env_key("PATH")
    assert not is_sensitive_env_key("HOME")


def test_sanitize_env_removes_credentials() -> None:
    env = {
        "DOOM_API_KEY": "secret",
        "AWS_SECRET_ACCESS_KEY": "secret",
        "MY_TOKEN": "tok",
        "PATH": "/usr/bin",
        "HOME": "/home/user",
        "SAFE_VAR": "ok",
    }
    sanitized = sanitize_env(env)
    assert "DOOM_API_KEY" not in sanitized
    assert "AWS_SECRET_ACCESS_KEY" not in sanitized
    assert "MY_TOKEN" not in sanitized
    assert sanitized["PATH"] == "/usr/bin"
    assert sanitized["SAFE_VAR"] == "ok"
    assert sanitized["HOME"] == "/home/user"


def test_run_guarded_argv_only_and_sanitized(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[list[str], dict]] = []

    class FakeProc:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(args: list[str], **kwargs: object) -> FakeProc:
        calls.append((args, kwargs))
        return FakeProc()

    monkeypatch.setattr("doom_team.subprocess_guard.network_isolation_available", lambda: False)
    monkeypatch.setattr("doom_team.subprocess_guard.subprocess.run", fake_run)
    run_guarded(["echo", "hi"], env={"PATH": "/usr/bin", "DOOM_API_KEY": "secret"})
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args == ["echo", "hi"]
    assert "shell" not in kwargs or kwargs["shell"] is False
    env = kwargs["env"]
    assert "DOOM_API_KEY" not in env
    assert env["PATH"] == "/usr/bin"


def test_run_guarded_network_isolation_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    class FakeProc:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(args: list[str], **kwargs: object) -> FakeProc:
        calls.append(args)
        return FakeProc()

    monkeypatch.setattr("doom_team.subprocess_guard.network_isolation_available", lambda: True)
    monkeypatch.setattr("doom_team.subprocess_guard.subprocess.run", fake_run)
    run_guarded(["echo", "hi"])
    assert calls and calls[0][:2] == ["unshare", "--net"]
    assert calls[0][2:] == ["echo", "hi"]


def test_run_guarded_no_network_isolation_when_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    class FakeProc:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(args: list[str], **kwargs: object) -> FakeProc:
        calls.append(args)
        return FakeProc()

    monkeypatch.setattr("doom_team.subprocess_guard.network_isolation_available", lambda: False)
    monkeypatch.setattr("doom_team.subprocess_guard.subprocess.run", fake_run)
    run_guarded(["echo", "hi"])
    assert calls and calls[0] == ["echo", "hi"]


def test_git_workspace_uses_guarded_subprocess(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[tuple[list[str], dict]] = []

    class FakeProc:
        stdout = ""
        stderr = ""
        returncode = 0

    def fake_run_guarded(args: list[str], **kwargs: object) -> FakeProc:
        calls.append((args, kwargs))
        return FakeProc()

    monkeypatch.setattr("doom_team.workspace.run_guarded", fake_run_guarded)
    ws = GitWorkspace(tmp_path / "ws")
    ws.create_workspace("task-1")
    assert len(calls) > 0
    for args, kwargs in calls:
        assert isinstance(args, list)
        assert args[0] == "git"
        assert "shell" not in kwargs or kwargs["shell"] is False


def test_workspace_pre_execution_symlink_check(tmp_path: Path) -> None:
    ws = GitWorkspace(tmp_path / "ws")
    ws_id = ws.create_workspace("task-1")
    path = ws._workspace_path(ws_id)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "file.txt").write_text("x")
    shutil.rmtree(path)
    path.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        ws.commit(ws_id, "message")


def test_threat_model_documents_controls() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    threat_model = repo_root / "runbooks" / "THREAT_MODEL.md"
    risks = repo_root / "docs" / "RISKS.md"
    assert threat_model.is_file()
    assert risks.is_file()
    threat_text = threat_model.read_text()
    risks_text = risks.read_text()
    assert "sanitized" in threat_text.lower()
    assert "argv-only" in threat_text.lower()
    assert "network isolation" in threat_text.lower()
    assert "not a complete security sandbox" in threat_text.lower()
    assert "sanitized" in risks_text.lower()
    assert "network isolation" in risks_text.lower()
