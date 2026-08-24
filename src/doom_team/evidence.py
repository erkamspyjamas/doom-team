"""In-memory evidence store for candidate-bound records."""

from __future__ import annotations

from typing import Any

from doom_team.ports import EvidenceStore


class InMemoryEvidenceStore(EvidenceStore):
    """A simple in-memory evidence store for testing and small runs."""

    def __init__(self) -> None:
        self._records: dict[str, dict[str, Any]] = {}

    def save(self, evidence: dict[str, Any]) -> str:
        """Persist evidence and return its id."""
        evidence_id = evidence.get("id", "")
        if not evidence_id:
            evidence_id = f"ev_{len(self._records)}"
            evidence = {**evidence, "id": evidence_id}
        self._records[evidence_id] = evidence
        return evidence_id

    def load(self, evidence_id: str) -> dict[str, Any]:
        """Load evidence by id."""
        return self._records[evidence_id]
