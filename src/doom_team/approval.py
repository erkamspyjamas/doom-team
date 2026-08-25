"""Durable ApprovalPort with fail-closed verification.

Approval records are stored on disk as JSON, bound to the exact frontier
SHA and the current Project Contract / Roadmap revisions. Verification
fails closed on any stale, missing, mismatched, or corrupt record. Writes
are atomic and fsynced.
"""

from __future__ import annotations

import datetime
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from doom_team.durable import atomic_write_text
from doom_team.observability import Event, EventSink
from doom_team.ports import ApprovalPort


class ApprovalRecordCorruptError(Exception):
    """Raised when an approval record is corrupt or malformed."""


@dataclass(frozen=True)
class ApprovalRecord:
    """A durable approval record bound to a specific frontier."""

    action: str
    frontier_sha: str
    operator: str
    decision: bool
    timestamp: str
    contract_revision: str
    roadmap_revision: str
    schema_version: int = 1
    digest: str = ""


def _utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _digest(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _validate_utc_iso(value: str) -> None:
    try:
        parsed = datetime.datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("timestamp must be ISO-8601 UTC") from exc
    offset = parsed.utcoffset()
    if offset is None or offset != datetime.timedelta(0):
        raise ValueError("timestamp must be ISO-8601 UTC")


class DurableApprovalPort(ApprovalPort):
    """File-backed ApprovalPort that fails closed on any invalid record."""

    def __init__(
        self,
        base_dir: str | Path,
        contract_revision: str = "contract-1",
        roadmap_revision: str = "roadmap-1",
        event_sink: EventSink | None = None,
    ) -> None:
        self.base_dir = Path(base_dir)
        self.approvals_dir = self.base_dir / "approvals"
        self.approvals_dir.mkdir(parents=True, exist_ok=True)
        self.contract_revision = contract_revision
        self.roadmap_revision = roadmap_revision
        self._event_sink = event_sink

    def _path(self, action: str, frontier_sha: str) -> Path:
        safe_action = action.replace("/", "_").replace("\\", "_")
        return self.approvals_dir / f"{safe_action}__{frontier_sha}.json"

    def _record_event(self, kind: str, details: dict[str, Any]) -> None:
        if self._event_sink is not None:
            self._event_sink.record(
                Event(kind=kind, details=details, producer_role="approval")
            )

    def approve(
        self,
        action: str,
        frontier_sha: str,
        operator: str,
        decision: bool = True,
        timestamp: str | None = None,
        contract_revision: str | None = None,
        roadmap_revision: str | None = None,
    ) -> str:
        """Record an approval durably and atomically; returns the digest."""
        if not action.strip() or not frontier_sha.strip() or not operator.strip():
            raise ValueError("action, frontier_sha, and operator must be non-empty")
        if contract_revision is None:
            contract_revision = self.contract_revision
        if roadmap_revision is None:
            roadmap_revision = self.roadmap_revision
        if not contract_revision or not roadmap_revision:
            raise ValueError("contract and roadmap revisions must be non-empty")
        if timestamp is None:
            timestamp = _utc_now_iso()
        _validate_utc_iso(timestamp)
        payload: dict[str, Any] = {
            "schema_version": 1,
            "action": action,
            "frontier_sha": frontier_sha,
            "operator": operator,
            "decision": bool(decision),
            "timestamp": timestamp,
            "contract_revision": contract_revision,
            "roadmap_revision": roadmap_revision,
        }
        digest = _digest(payload)
        payload["digest"] = digest
        path = self._path(action, frontier_sha)
        atomic_write_text(path, json.dumps(payload, indent=2, sort_keys=True))
        self._record_event(
            "APPROVAL_RECORDED",
            {
                "action": action,
                "frontier_sha": frontier_sha,
                "operator": operator,
                "decision": bool(decision),
                "contract_revision": contract_revision,
                "roadmap_revision": roadmap_revision,
                "digest": digest,
            },
        )
        return digest

    def load(self, action: str, frontier_sha: str) -> ApprovalRecord:
        """Load and validate an approval record; raises on corrupt/malformed data."""
        path = self._path(action, frontier_sha)
        if not path.exists():
            raise FileNotFoundError(f"No approval record for {action} at {frontier_sha}")
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            raise ApprovalRecordCorruptError(f"Corrupt approval record: {exc}") from exc
        if not isinstance(data, dict):
            raise ApprovalRecordCorruptError("Approval record is not a dict")
        required = (
            "schema_version",
            "action",
            "frontier_sha",
            "operator",
            "decision",
            "timestamp",
            "contract_revision",
            "roadmap_revision",
            "digest",
        )
        missing = [k for k in required if k not in data]
        if missing:
            raise ApprovalRecordCorruptError(f"Approval record missing fields: {missing}")
        if not isinstance(data["schema_version"], int) or data["schema_version"] != 1:
            raise ApprovalRecordCorruptError("Unsupported schema_version")
        for field in (
            "action",
            "frontier_sha",
            "operator",
            "timestamp",
            "contract_revision",
            "roadmap_revision",
            "digest",
        ):
            value = data[field]
            if not isinstance(value, str) or not value:
                raise ApprovalRecordCorruptError(f"Invalid {field}")
        if not isinstance(data["decision"], bool):
            raise ApprovalRecordCorruptError("Invalid decision")
        if data["action"] != action or data["frontier_sha"] != frontier_sha:
            raise ApprovalRecordCorruptError("Record does not match request")
        try:
            _validate_utc_iso(data["timestamp"])
        except ValueError as exc:
            raise ApprovalRecordCorruptError("Invalid timestamp") from exc
        digest = data.pop("digest")
        if digest != _digest(data):
            raise ApprovalRecordCorruptError("Approval record digest mismatch")
        return ApprovalRecord(**data)

    def request_approval(self, action: str, context: dict[str, Any]) -> bool:
        """Return True only for a valid, current, non-corrupt approval record."""
        frontier_sha = context.get("frontier_sha")
        if not frontier_sha or not isinstance(frontier_sha, str):
            self._record_event(
                "APPROVAL_ERROR", {"action": action, "reason": "missing frontier_sha"}
            )
            return False
        try:
            record = self.load(action, frontier_sha)
        except (FileNotFoundError, ApprovalRecordCorruptError) as exc:
            self._record_event(
                "APPROVAL_ERROR",
                {"action": action, "frontier_sha": frontier_sha, "reason": str(exc)},
            )
            return False
        if record.decision is not True:
            self._record_event(
                "APPROVAL_DENIED",
                {"action": action, "frontier_sha": frontier_sha, "reason": "decision false"},
            )
            return False
        ok = (
            record.contract_revision == self.contract_revision
            and record.roadmap_revision == self.roadmap_revision
        )
        if not ok:
            self._record_event(
                "APPROVAL_DENIED",
                {
                    "action": action,
                    "frontier_sha": frontier_sha,
                    "reason": "stale revisions",
                },
            )
        return ok
