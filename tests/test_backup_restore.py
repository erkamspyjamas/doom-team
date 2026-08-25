"""Tests for durable-state lifecycle: corruption fail-closed, backup+restore,
and restart recovery."""

import json
from pathlib import Path

import pytest

from doom_team.backup import backup_state, restore_state, verify_backup
from doom_team.durable import StateCorruptionError
from doom_team.evidence import DurableEvidenceStore
from doom_team.gas_city_adapter import GasCityAdapter
from doom_team.recovery import RecoveryController
from doom_team.verdicts import DurableVerdictStore, Verdict


def _populate_state(state_dir: Path) -> None:
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
    controller = RecoveryController(state_dir / "recovery.json")
    controller.initialize("task-1", semantic_retries=2)


def _corrupt_json(path: Path) -> None:
    data = json.loads(path.read_text())
    data["candidate_commit"] = "tampered"
    path.write_text(json.dumps(data))


def test_evidence_load_fails_closed_on_corruption(tmp_path: Path) -> None:
    store = DurableEvidenceStore(tmp_path)
    store.save(
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
    path = store.evidence_dir / "ev-1.json"
    _corrupt_json(path)
    with pytest.raises(StateCorruptionError):
        store.load("ev-1")


def test_verdict_load_fails_closed_on_corruption(tmp_path: Path) -> None:
    store = DurableVerdictStore(tmp_path)
    vid = store.save(Verdict(candidate_id="cand-1", role="QA", decision=True))
    path = store.verdict_dir / f"{vid}.json"
    data = json.loads(path.read_text())
    data["decision"] = False
    path.write_text(json.dumps(data))
    with pytest.raises(StateCorruptionError):
        store.load(vid)


def test_recovery_load_fails_closed_on_corruption(tmp_path: Path) -> None:
    path = tmp_path / "recovery.json"
    controller = RecoveryController(path)
    controller.initialize("task-1", semantic_retries=2)
    data = json.loads(path.read_text())
    data["task-1"]["semantic_retries_left"] = 99
    path.write_text(json.dumps(data))
    with pytest.raises(StateCorruptionError):
        RecoveryController(path)


def test_gas_adapter_load_fails_closed_on_corruption(tmp_path: Path) -> None:
    class FakeClient:
        def enqueue(self, task, dependencies=()):
            return "gas_1"

    store_path = tmp_path / "store.json"
    adapter = GasCityAdapter(FakeClient(), store_path)
    adapter.enqueue("task")
    data = json.loads(store_path.read_text())
    data["doom_x"] = "tampered"
    store_path.write_text(json.dumps(data))
    with pytest.raises(StateCorruptionError):
        GasCityAdapter(FakeClient(), store_path)


def test_backup_restore_round_trip(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    backup_root = tmp_path / "backups"
    _populate_state(state_dir)

    backup_path = backup_state(state_dir, backup_root)
    assert verify_backup(backup_path)

    # Modify the live state to prove restore recovers the backup.
    ev_store = DurableEvidenceStore(state_dir)
    ev_store.save(
        {
            "id": "ev-2",
            "candidate_id": "cand-2",
            "candidate_commit": "def",
            "objective_id": "obj-1",
            "mission_id": "mission-1",
            "producer_role": "dev",
            "producer_run": "run-2",
            "command": "make test",
            "artifacts": [],
            "actual_model": "deepseek-v4",
            "actual_runtime": "local",
            "timestamp": "2024-01-01T00:00:00+00:00",
            "digest": "sha256:def",
        }
    )

    restore_state(backup_path, state_dir)
    restored_ev = DurableEvidenceStore(state_dir)
    loaded = restored_ev.load("ev-1")
    assert loaded["id"] == "ev-1"
    assert not (restored_ev.evidence_dir / "ev-2.json").exists()

    # Verdict and recovery are also restored.
    verdict_store = DurableVerdictStore(state_dir)
    verdicts = list(verdict_store.verdict_dir.glob("*.json"))
    assert len(verdicts) == 1
    controller = RecoveryController(state_dir / "recovery.json")
    assert controller.state("task-1") is not None


def test_restore_requires_valid_backup(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    bad_backup = tmp_path / "bad-backup"
    bad_backup.mkdir()
    (bad_backup / "random.txt").write_text("data")
    with pytest.raises(StateCorruptionError):
        restore_state(bad_backup, state_dir)


def test_restart_recovery_after_backup_restore(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    backup_root = tmp_path / "backups"
    _populate_state(state_dir)
    backup_path = backup_state(state_dir, backup_root)

    # Exhaust a task in the live state.
    controller = RecoveryController(state_dir / "recovery.json")
    controller.initialize("task-2", semantic_retries=1)
    epoch = controller.initialize("task-2", semantic_retries=1)
    from doom_team.recovery import Blocker
    controller.on_semantic_blocker("task-2", epoch.attempt, Blocker("qa", "failed"))
    assert controller.is_exhausted("task-2")

    # Restore the backup (which predates the exhaustion).
    restore_state(backup_path, state_dir)
    restored = RecoveryController(state_dir / "recovery.json")
    assert restored.state("task-2") is None
    assert not restored.is_exhausted("task-2")
    assert restored.next_attempt("task-2") is not None
