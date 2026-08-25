"""Tests for the staging end-to-end verification harness."""

from pathlib import Path

from staging.run_staging import StagingHarness


def test_staging_harness_passes(tmp_path: Path) -> None:
    harness = StagingHarness(tmp_path, mode="fake")
    report = harness.run()
    assert report["all_passed"] is True
    assert len(report["scenarios"]) == 15
