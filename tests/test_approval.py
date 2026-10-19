"""Tests for the durable ApprovalPort and its fail-closed verification."""

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from doom_team.approval import ApprovalRecordCorruptError, DurableApprovalPort


def _make_port(
    tmp_path: Path,
    contract_revision: str = "contract-1",
    roadmap_revision: str = "roadmap-1",
) -> DurableApprovalPort:
    return DurableApprovalPort(
        tmp_path / "approvals",
        contract_revision=contract_revision,
        roadmap_revision=roadmap_revision,
    )


def test_approve_binds_required_fields(tmp_path: Path) -> None:
    port = _make_port(tmp_path)
    port.approve("release", "sha123", "ops-1", timestamp="2024-01-01T00:00:00+00:00")
    record_path = port._path("release", "sha123")
    data = json.loads(record_path.read_text())
    assert data["action"] == "release"
    assert data["frontier_sha"] == "sha123"
    assert data["operator"] == "ops-1"
    assert data["decision"] is True
    assert data["timestamp"] == "2024-01-01T00:00:00+00:00"
    assert data["contract_revision"] == "contract-1"
    assert data["roadmap_revision"] == "roadmap-1"
    assert data["digest"].startswith("sha256:")


@pytest.mark.parametrize("decision", [True, False], ids=("approve", "deny"))
def test_load_preserves_complete_record_and_digest_across_restart(tmp_path: Path, decision: bool) -> None:
    port = _make_port(tmp_path)
    digest = port.approve(
        "release", "sha123", "ops-1", decision=decision,
        timestamp="2024-01-01T00:00:00+00:00",
    )
    path = port._path("release", "sha123")
    before = path.read_bytes()
    persisted = json.loads(before)
    restarted = _make_port(tmp_path)
    loaded = restarted.load("release", "sha123")
    assert loaded.digest == digest == persisted["digest"]
    assert asdict(loaded) == persisted  # Audit consumers must retain every verified field.
    assert restarted.request_approval("release", {"frontier_sha": "sha123"}) is decision
    assert path.read_bytes() == before


@pytest.mark.parametrize("digest", ["", "sha256:" + "0" * 64], ids=("empty", "incorrect"))
def test_load_rejects_corrupted_digest(tmp_path: Path, digest: str) -> None:
    port = _make_port(tmp_path)
    port.approve("release", "sha123", "ops-1")
    path = port._path("release", "sha123")
    data = json.loads(path.read_text())
    data["digest"] = digest
    path.write_text(json.dumps(data))
    before = path.read_bytes()
    with pytest.raises(ApprovalRecordCorruptError):
        port.load("release", "sha123")
    assert port.request_approval("release", {"frontier_sha": "sha123"}) is False
    assert path.read_bytes() == before


def test_request_approval_true_when_valid(tmp_path: Path) -> None:
    port = _make_port(tmp_path)
    port.approve("release", "sha123", "ops-1")
    assert port.request_approval("release", {"frontier_sha": "sha123"}) is True


def test_missing_record_fails_closed(tmp_path: Path) -> None:
    port = _make_port(tmp_path)
    assert port.request_approval("release", {"frontier_sha": "nope"}) is False


def test_wrong_sha_fails_closed(tmp_path: Path) -> None:
    port = _make_port(tmp_path)
    port.approve("release", "sha123", "ops-1")
    assert port.request_approval("release", {"frontier_sha": "sha456"}) is False


def test_stale_revision_fails_closed(tmp_path: Path) -> None:
    port = _make_port(tmp_path, contract_revision="contract-1")
    port.approve("release", "sha123", "ops-1", contract_revision="contract-0")
    port2 = DurableApprovalPort(
        tmp_path / "approvals",
        contract_revision="contract-1",
    )
    assert port2.request_approval("release", {"frontier_sha": "sha123"}) is False


def test_missing_frontier_sha_in_context_fails_closed(tmp_path: Path) -> None:
    port = _make_port(tmp_path)
    port.approve("release", "sha123", "ops-1")
    assert port.request_approval("release", {}) is False


def test_decision_false_fails_closed(tmp_path: Path) -> None:
    port = _make_port(tmp_path)
    port.approve("release", "sha123", "ops-1", decision=False)
    assert port.request_approval("release", {"frontier_sha": "sha123"}) is False


def test_corrupt_json_fails_closed_and_load_raises(tmp_path: Path) -> None:
    port = _make_port(tmp_path)
    record_path = port._path("release", "sha123")
    record_path.parent.mkdir(parents=True, exist_ok=True)
    record_path.write_text("{not json")
    with pytest.raises(ApprovalRecordCorruptError):
        port.load("release", "sha123")
    assert port.request_approval("release", {"frontier_sha": "sha123"}) is False


def test_malformed_record_fails_closed(tmp_path: Path) -> None:
    port = _make_port(tmp_path)
    record_path = port._path("release", "sha123")
    record_path.parent.mkdir(parents=True, exist_ok=True)
    record_path.write_text(json.dumps({"action": "release"}))
    with pytest.raises(ApprovalRecordCorruptError):
        port.load("release", "sha123")
    assert port.request_approval("release", {"frontier_sha": "sha123"}) is False


def test_tampered_record_detected(tmp_path: Path) -> None:
    port = _make_port(tmp_path)
    port.approve("release", "sha123", "ops-1")
    record_path = port._path("release", "sha123")
    data = json.loads(record_path.read_text())
    data["operator"] = "attacker"
    record_path.write_text(json.dumps(data))
    with pytest.raises(ApprovalRecordCorruptError):
        port.load("release", "sha123")
    assert port.request_approval("release", {"frontier_sha": "sha123"}) is False


def test_non_utc_timestamp_rejected(tmp_path: Path) -> None:
    port = _make_port(tmp_path)
    with pytest.raises(ValueError, match="ISO-8601 UTC"):
        port.approve("release", "sha123", "ops-1", timestamp="2024-01-01T00:00:00+01:00")


def test_approval_never_performs_irreversible_actions(tmp_path: Path) -> None:
    port = _make_port(tmp_path)
    for method in ("deploy", "merge", "push"):
        assert not hasattr(port, method)


def test_atomic_write_leaves_no_temp(tmp_path: Path) -> None:
    port = _make_port(tmp_path)
    port.approve("release", "sha123", "ops-1")
    assert list((tmp_path / "approvals").glob("*.tmp")) == []
