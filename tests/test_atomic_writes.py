"""Tests for crash-safe atomic durable writes."""

from __future__ import annotations

import threading
from pathlib import Path

from doom_team.approval import DurableApprovalPort
from doom_team.backup import backup_state, restore_state, verify_backup
from doom_team.durable import atomic_write_json, read_json_checked
from doom_team.evidence import DurableEvidenceStore


def test_normal_atomic_write_leaves_no_temp(tmp_path: Path) -> None:
    dest = tmp_path / "state.json"
    atomic_write_json(dest, {"a": 1})
    assert read_json_checked(dest) == {"a": 1}
    assert [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")] == []


def test_stale_temp_never_blocks_subsequent_write(tmp_path: Path) -> None:
    dest = tmp_path / "state.json"
    atomic_write_json(dest, {"a": 1})
    # Simulate crash debris: a legacy fixed-name temp and a modern unique temp.
    legacy = tmp_path / "state.json.tmp"
    legacy.write_text("garbage")
    unique = tmp_path / "state.json.ABCDEF.tmp"
    unique.write_text("garbage")
    atomic_write_json(dest, {"b": 2})
    assert read_json_checked(dest) == {"b": 2}
    # Stale temps are preserved (never deleted) and never read as destination.
    assert legacy.read_text() == "garbage"
    assert unique.read_text() == "garbage"


def test_destination_valid_while_stale_temp_exists(tmp_path: Path) -> None:
    dest = tmp_path / "state.json"
    atomic_write_json(dest, {"a": 1})
    (tmp_path / "state.json.ABC.tmp").write_text("junk")
    assert read_json_checked(dest) == {"a": 1}


def test_concurrent_writers_produce_valid_final_state(tmp_path: Path) -> None:
    dest = tmp_path / "state.json"
    threads = [
        threading.Thread(
            target=atomic_write_json,
            args=(dest, {"writer": i, "payload": "x" * 4096}),
        )
        for i in range(8)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    data = read_json_checked(dest)  # checksum-verified => no torn state
    assert data["writer"] in range(8)
    assert len(data["payload"]) == 4096


def test_successive_writes_do_not_accumulate_temps(tmp_path: Path) -> None:
    dest = tmp_path / "state.json"
    for i in range(5):
        atomic_write_json(dest, {"n": i})
    assert [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")] == []


def test_unrelated_neighbor_never_deleted(tmp_path: Path) -> None:
    dest = tmp_path / "state.json"
    neighbor = tmp_path / "unrelated.txt"
    neighbor.write_text("keep me")
    atomic_write_json(dest, {"a": 1})
    assert neighbor.read_text() == "keep me"


def test_backup_restore_round_trip(tmp_path: Path) -> None:
    state = tmp_path / "state"
    store = DurableEvidenceStore(state)
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
    backup_path = backup_state(state, tmp_path / "backups")
    assert verify_backup(backup_path)
    restore_state(backup_path, state)
    assert (state / "evidence").is_dir()


def test_approval_store_round_trip(tmp_path: Path) -> None:
    port = DurableApprovalPort(tmp_path / "approvals")
    port.approve("release", "sha1", "ops", decision=True)
    assert port.request_approval("release", {"frontier_sha": "sha1"}) is True
