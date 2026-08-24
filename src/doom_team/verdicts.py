"""Verdict store and value objects for QA/Reviewer validation."""

from __future__ import annotations

import datetime
import json
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class Verdict:
    """A structured validation verdict for a candidate."""

    candidate_id: str
    role: str
    decision: bool
    reason: str = ""
    timestamp: datetime.datetime = datetime.datetime.now(datetime.timezone.utc)


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
    """File-backed verdict store that writes JSON records."""

    def __init__(self, base_dir: str | Path) -> None:
        self.base_dir = Path(base_dir)
        self.verdict_dir = self.base_dir / "verdicts"
        self.verdict_dir.mkdir(parents=True, exist_ok=True)

    def save(self, verdict: Verdict) -> str:
        verdict_id = f"verdict_{uuid.uuid4().hex[:8]}"
        with open(self.verdict_dir / f"{verdict_id}.json", "w") as f:
            json.dump(asdict(verdict), f, default=str, indent=2)
        return verdict_id

    def load(self, verdict_id: str) -> Verdict:
        with open(self.verdict_dir / f"{verdict_id}.json") as f:
            data = json.load(f)
        return Verdict(**data)
