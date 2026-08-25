"""Independent read-only QA and Reviewer validation.

Validators receive only the candidate and its evidence; no workspace or
repository access is exposed, so they cannot mutate any state. Validation
verifies the evidence is bound to the exact candidate before invoking the
provided verification callable. Structured verdicts are persisted durably.
"""

from __future__ import annotations

import datetime
import json
from dataclasses import asdict
from pathlib import Path
from typing import Callable

from doom_team.domain import Candidate, Evidence
from doom_team.verdicts import Verdict, VerdictStore


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
        verdict = Verdict(
            candidate_id=candidate.id,
            role=role,
            decision=False,
            reason="Evidence is not bound to this candidate",
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        verdict_store.save(verdict)
        return verdict

    passed, message = verify(candidate, evidence)
    verdict = Verdict(
        candidate_id=candidate.id,
        role=role,
        decision=passed,
        reason=message,
        timestamp=datetime.datetime.now(datetime.timezone.utc),
    )
    verdict_store.save(verdict)
    return verdict


class DurableVerdictStore:
    """File-backed verdict store for durable, structured verdicts."""

    def __init__(self, base_dir: str | Path) -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def save(self, verdict: Verdict) -> str:
        timestamp = datetime.datetime.now().timestamp()
        verdict_id = f"verdict_{timestamp:.0f}_{hash(verdict) & 0xffffff:06x}"
        (self.base_dir / f"{verdict_id}.json").write_text(
            json.dumps(asdict(verdict), indent=2, default=str)
        )
        return verdict_id

    def load(self, verdict_id: str) -> Verdict:
        data = json.loads((self.base_dir / f"{verdict_id}.json").read_text())
        if not isinstance(data, dict):
            raise ValueError(f"Verdict record {verdict_id} is not a dict")
        return Verdict(**data)
