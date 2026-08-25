"""Regression tests for CLI state-dir precedence.

An explicit ``--state-dir`` must always win over ``DOOM_STATE_DIR``, whether
the flag appears before or after the subcommand. We assert which physical
directory is actually used for backup, restore, approve, status, doctor, and
serve.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from doom_team.backup import backup_state
from doom_team.cli import main


def _write_marker(directory: Path, name: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text("marker")


def _backup_contains(backup_root: Path, name: str) -> bool:
    return any((backup / name).is_file() for backup in backup_root.glob("doom-backup-*"))


def _set_config_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOOM_API_KEY", "secret")
    monkeypatch.setenv("DOOM_MODEL_PROVIDER", "nvidia")
    monkeypatch.setenv("DOOM_MODEL_NAME", "deepseek-v4")


# --- backup ----------------------------------------------------------------


def test_backup_subparser_position_cli_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cli_dir = tmp_path / "cli-state"
    env_dir = tmp_path / "env-state"
    _write_marker(cli_dir, "cli.txt")
    _write_marker(env_dir, "env.txt")
    monkeypatch.setenv("DOOM_STATE_DIR", str(env_dir))
    backup_root = tmp_path / "backups"
    rc = main(["backup", "--state-dir", str(cli_dir), "--backup-dir", str(backup_root)])
    assert rc == 0
    assert _backup_contains(backup_root, "cli.txt")
    assert not _backup_contains(backup_root, "env.txt")


def test_backup_parent_position_cli_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cli_dir = tmp_path / "cli-state"
    env_dir = tmp_path / "env-state"
    _write_marker(cli_dir, "cli.txt")
    _write_marker(env_dir, "env.txt")
    monkeypatch.setenv("DOOM_STATE_DIR", str(env_dir))
    backup_root = tmp_path / "backups"
    rc = main(["--state-dir", str(cli_dir), "backup", "--backup-dir", str(backup_root)])
    assert rc == 0
    assert _backup_contains(backup_root, "cli.txt")
    assert not _backup_contains(backup_root, "env.txt")


def test_backup_cli_only_no_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DOOM_STATE_DIR", raising=False)
    cli_dir = tmp_path / "cli-state"
    _write_marker(cli_dir, "cli.txt")
    backup_root = tmp_path / "backups"
    rc = main(["backup", "--state-dir", str(cli_dir), "--backup-dir", str(backup_root)])
    assert rc == 0
    assert _backup_contains(backup_root, "cli.txt")


def test_backup_env_used_when_no_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_dir = tmp_path / "env-state"
    _write_marker(env_dir, "env.txt")
    monkeypatch.setenv("DOOM_STATE_DIR", str(env_dir))
    backup_root = tmp_path / "backups"
    rc = main(["backup", "--backup-dir", str(backup_root)])
    assert rc == 0
    assert _backup_contains(backup_root, "env.txt")


def test_backup_missing_state_dir_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DOOM_STATE_DIR", raising=False)
    rc = main(["backup", "--backup-dir", str(tmp_path / "backups")])
    assert rc == 1


# --- restore ---------------------------------------------------------------


def test_restore_subparser_position_cli_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cli_dir = tmp_path / "cli-state"
    env_dir = tmp_path / "env-state"
    _write_marker(cli_dir, "cli.txt")
    _write_marker(env_dir, "env.txt")
    backup_path = backup_state(cli_dir, tmp_path / "backups")
    monkeypatch.setenv("DOOM_STATE_DIR", str(env_dir))
    rc = main(["restore", "--state-dir", str(cli_dir), "--backup-path", str(backup_path)])
    assert rc == 0
    assert (cli_dir / "cli.txt").is_file()
    assert (env_dir / "env.txt").is_file()
    assert not (env_dir / "cli.txt").exists()


def test_restore_parent_position_cli_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cli_dir = tmp_path / "cli-state"
    env_dir = tmp_path / "env-state"
    _write_marker(cli_dir, "cli.txt")
    _write_marker(env_dir, "env.txt")
    backup_path = backup_state(cli_dir, tmp_path / "backups")
    monkeypatch.setenv("DOOM_STATE_DIR", str(env_dir))
    rc = main(["--state-dir", str(cli_dir), "restore", "--backup-path", str(backup_path)])
    assert rc == 0
    assert (cli_dir / "cli.txt").is_file()
    assert (env_dir / "env.txt").is_file()
    assert not (env_dir / "cli.txt").exists()


# --- approve ---------------------------------------------------------------


def test_approve_subparser_position_cli_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cli_dir = tmp_path / "cli-state"
    env_dir = tmp_path / "env-state"
    monkeypatch.setenv("DOOM_STATE_DIR", str(env_dir))
    _set_config_env(monkeypatch)
    rc = main(
        [
            "approve",
            "--state-dir",
            str(cli_dir),
            "--action",
            "release",
            "--frontier-sha",
            "sha1",
            "--operator",
            "ops",
            "--decision",
            "approve",
        ]
    )
    assert rc == 0
    assert (cli_dir / "approvals").is_dir()
    assert not (env_dir / "approvals").exists()


def test_approve_parent_position_cli_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cli_dir = tmp_path / "cli-state"
    env_dir = tmp_path / "env-state"
    monkeypatch.setenv("DOOM_STATE_DIR", str(env_dir))
    _set_config_env(monkeypatch)
    rc = main(
        [
            "--state-dir",
            str(cli_dir),
            "approve",
            "--action",
            "release",
            "--frontier-sha",
            "sha1",
            "--operator",
            "ops",
            "--decision",
            "approve",
        ]
    )
    assert rc == 0
    assert (cli_dir / "approvals").is_dir()
    assert not (env_dir / "approvals").exists()


# --- status / doctor -------------------------------------------------------


def test_doctor_subparser_position_cli_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cli_dir = tmp_path / "cli-state"
    env_dir = tmp_path / "env-state"
    monkeypatch.setenv("DOOM_STATE_DIR", str(env_dir))
    _set_config_env(monkeypatch)
    rc = main(["doctor", "--state-dir", str(cli_dir)])
    assert rc == 0
    assert (cli_dir / "evidence").is_dir()
    assert not (env_dir / "evidence").exists()


def test_doctor_parent_position_cli_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cli_dir = tmp_path / "cli-state"
    env_dir = tmp_path / "env-state"
    monkeypatch.setenv("DOOM_STATE_DIR", str(env_dir))
    _set_config_env(monkeypatch)
    rc = main(["--state-dir", str(cli_dir), "doctor"])
    assert rc == 0
    assert (cli_dir / "evidence").is_dir()
    assert not (env_dir / "evidence").exists()


def test_status_subparser_position_cli_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cli_dir = tmp_path / "cli-state"
    env_dir = tmp_path / "env-state"
    monkeypatch.setenv("DOOM_STATE_DIR", str(env_dir))
    _set_config_env(monkeypatch)
    rc = main(["status", "--state-dir", str(cli_dir)])
    assert rc == 0
    assert (cli_dir / "evidence").is_dir()
    assert not (env_dir / "evidence").exists()


def test_status_parent_position_cli_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cli_dir = tmp_path / "cli-state"
    env_dir = tmp_path / "env-state"
    monkeypatch.setenv("DOOM_STATE_DIR", str(env_dir))
    _set_config_env(monkeypatch)
    rc = main(["--state-dir", str(cli_dir), "status"])
    assert rc == 0
    assert (cli_dir / "evidence").is_dir()
    assert not (env_dir / "evidence").exists()


# --- serve (subprocess) ----------------------------------------------------


def _serve_argv(prefix: bool, cli_dir: Path) -> list[str]:
    if prefix:
        return ["--state-dir", str(cli_dir), "serve"]
    return ["serve", "--state-dir", str(cli_dir)]


@pytest.mark.parametrize("prefix", [False, True])
def test_serve_cli_wins_over_env(tmp_path: Path, prefix: bool) -> None:
    cli_dir = tmp_path / "cli-state"
    env_dir = tmp_path / "env-state"
    _write_marker(env_dir, "env.txt")
    env = dict(os.environ)
    env["DOOM_STATE_DIR"] = str(env_dir)
    env["DOOM_API_KEY"] = "secret"
    env["DOOM_MODEL_PROVIDER"] = "nvidia"
    env["DOOM_MODEL_NAME"] = "deepseek-v4"
    argv = _serve_argv(prefix, cli_dir)
    proc = subprocess.Popen(
        [sys.executable, "-c", "from doom_team.cli import main; main()", *argv],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        assert proc.stdout is not None
        line = proc.stdout.readline().strip()
        assert line == "Doom Team service ready"
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=5)
        assert proc.returncode == 0
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    assert (cli_dir / "evidence").is_dir()
    assert not (env_dir / "evidence").exists()
