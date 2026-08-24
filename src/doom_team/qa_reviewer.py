"""Independent read-only QA and Reviewer validation.

Validators receive only the candidate and its evidence; no workspace or
repository access is exposed, so they cannot mutate any state. Validation
verifies the evidence is bound to the exact candidate before invoking the
provided verification callable. Structured verdicts are persisted durably.
"""

from __future__ import annotations

import datetime
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from doom_team.domain import Candidate, Evidence


@dataclass(frozen=True)
class Verdict:
    """Structured outcome of a QA or Reviewer validation."""

    role: str
    candidate_id: str
    passed: bool
    message: str
    producer_run: str
    timestamp: datetime.datetime


class VerdictStore(Protocol):
    def save(self, verdict: dict[str, Any]) -> str:
        """Persist a verdict and return its id."""

    def load(self, verdict_id: str) -> dict[str, Any]:
        """Load a verdict by id."""


def run_validation(
    candidate: Candidate,
    evidence: Evidence,
    role: str,
    producer_run: str,
    verdict_store: VerdictStore,
    verify: Callable[[Candidate, Evidence], tuple[bool, str]],
) -> Verdict:
    """Run a read-only validation of evidence for a candidate.

    The caller must provide only the candidate and evidence; no workspace or
    repository handle is passed, so QA/Reviewer cannot mutate anything. The
    evidence must be bound to the exact candidate (same id and commit) or the
    validation fails without invoking ``verify``.
    """
    if role not in ("qa", "reviewer"):
        raise ValueError(f"Invalid validation role: {role}")

    if (
        evidence.candidate.id != candidate.id
        or evidence.candidate.candidate_commit != candidate.candidate_commit
    ):
        return Verdict(
            role=role,
            candidate_id=candidate.id,
            passed=False,
            message="Evidence is not bound to this candidate",
            producer_run=producer_run,
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )

    passed, message = verify(candidate, evidence)
    verdict = Verdict(
        role=role,
        candidate_id=candidate.id,
        passed=passed,
        message=message,
        producer_run=producer_run,
        timestamp=datetime.datetime.now(datetime.timezone.utc),
    )
    verdict_store.save(
        {
            "role": verdict.role,
            "candidate_id": verdict.candidate_id,
            "passed": verdict.passed,
            "message": verdict.message,
            "producer_run": verdict.producer_run,
            "timestamp": verdict.timestamp.isoformat(),
        }
    )
    return verdict


class DurableVerdictStore:
    """File-backed verdict store for durable, structured verdicts."""

    def __init__(self, base_dir: str | Path) -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def save(self, verdict: dict[str, Any]) -> str:
        verdict_id = verdict.get("id") or f"verdict_{len(list(self.base_dir.glob('*.json')))}"
        verdict = {**verdict, "id": verdict_id}
        (self.base_dir / f"{verdict_id}.json").write_text(
            json.dumps(verdict, indent=2, default=str)
        )
        return verdict_id

    def load(self, verdict_id: str) -> dict[str, Any]:
        return json.loads((self.base_dir / f"{verdict_id}.json").read_text())
