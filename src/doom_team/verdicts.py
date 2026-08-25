"""Verdict store and value objects for QA/Reviewer validation."""

from __future__ import annotations

import datetime
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from doom_team.durable import atomic_write_json, read_json_checked


@dataclass(frozen=True)
class Verdict:
    """A structured validation verdict for a candidate."""

    candidate_id: str
    role: str
    decision: bool
    reason: str = ""
    timestamp: datetime.datetime = field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc)
    )


@runtime_checkable
class VerdictStore(Protocol):
    """Port for persisting and loading verdicts."""

    def save(self, verdict: Verdict) -> str:
        """Persist a verdict and return its id."""
        ...

    def load(self, verdict_id: str) -> Verdict:
        """Load a verdict by id."""
        ...


class InMemoryVerdictStore(VerdictStore):
    """In-memory verdict store for tests and small runs."""

    def __init__(self) -> None:
        self._records: dict[str, Verdict] = {}

    def save(self, verdict: Verdict) -> str:
        verdict_id = f"verdict_{uuid.uuid4().hex[:8]}"
        self._records[verdict_id] = verdict
        return verdict_id

    def load(self, verdict_id: str) -> Verdict:
        return self._records[verdict_id]


class DurableVerdictStore(VerdictStore):
    """File-backed verdict store that writes JSON records atomically with checksums."""

    def __init__(self, base_dir: str | Path) -> None:
        self.base_dir = Path(base_dir)
        self.verdict_dir = self.base_dir / "verdicts"
        self.verdict_dir.mkdir(parents=True, exist_ok=True)

    def save(self, verdict: Verdict) -> str:
        verdict_id = f"verdict_{uuid.uuid4().hex[:8]}"
        atomic_write_json(self.verdict_dir / f"{verdict_id}.json", asdict(verdict))
        return verdict_id

    def load(self, verdict_id: str) -> Verdict:
        data = read_json_checked(self.verdict_dir / f"{verdict_id}.json")
        return Verdict(**data)
