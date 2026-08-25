"""Tests for the staging harness mode guard (only controlled/fake staging)."""

from __future__ import annotations

from pathlib import Path

from staging.run_staging import main


def test_staging_rejects_real_mode(tmp_path: Path) -> None:
    rc = main(
        [
            "--base-dir",
            str(tmp_path / "run"),
            "--output",
            str(tmp_path / "out.json"),
            "--mode",
            "real",
        ]
    )
    assert rc == 1
    assert not (tmp_path / "out.json").exists()


def test_staging_rejects_any_non_fake_mode(tmp_path: Path) -> None:
    rc = main(
        [
            "--base-dir",
            str(tmp_path / "run"),
            "--output",
            str(tmp_path / "out.json"),
            "--mode",
            "garbage",
        ]
    )
    assert rc == 1
    assert not (tmp_path / "out.json").exists()
