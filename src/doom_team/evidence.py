"""Evidence store implementations and freshness validation.

Provides in-memory and durable (file-backed) stores that persist candidate-bound
evidence records. Durable stores copy artifacts into the store directory so that
artifacts remain available after the original files are gone. Freshness checks
prevent stale evidence from being used to accept a newer candidate. All durable
writes are atomic and all reads fail closed on corruption.
"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import Any

from doom_team.domain import Candidate, Evidence
from doom_team.durable import atomic_write_json, read_json_checked
from doom_team.ports import EvidenceStore


class InMemoryEvidenceStore(EvidenceStore):
    """A simple in-memory evidence store for testing and small runs."""

    def __init__(self) -> None:
        self._records: dict[str, dict[str, Any]] = {}

    def save(self, evidence: dict[str, Any]) -> str:
        """Persist evidence and return its id."""
        evidence_id = str(evidence.get("id", ""))
        if not evidence_id:
            evidence_id = f"ev_{len(self._records)}"
            evidence = {**evidence, "id": evidence_id}
        self._records[evidence_id] = evidence
        return evidence_id

    def load(self, evidence_id: str) -> dict[str, Any]:
        """Load evidence by id."""
        return self._records[evidence_id]


class DurableEvidenceStore(EvidenceStore):
    """A file-backed evidence store that copies artifacts into a managed directory."""

    def __init__(self, base_dir: str | Path) -> None:
        self.base_dir = Path(base_dir)
        self.evidence_dir = self.base_dir / "evidence"
        self.artifacts_dir = self.base_dir / "artifacts"
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

    def save(self, evidence: dict[str, Any]) -> str:
        """Persist evidence, copying any artifact files into the store, and return its id."""
        evidence_id = str(evidence.get("id", ""))
        if not evidence_id:
            evidence_id = f"ev_{uuid.uuid4().hex[:8]}"
            evidence = {**evidence, "id": evidence_id}

        artifact_paths = evidence.get("artifacts", [])
        stored_artifacts: list[str] = []
        for artifact in artifact_paths:
            src = Path(artifact)
            if src.is_file():
                dest_dir = self.artifacts_dir / evidence_id
                dest_dir.mkdir(exist_ok=True)
                dest = dest_dir / src.name
                shutil.copy2(src, dest)
                stored_artifacts.append(str(dest))
            else:
                stored_artifacts.append(str(artifact))
        evidence["artifacts"] = stored_artifacts

        atomic_write_json(self.evidence_dir / f"{evidence_id}.json", evidence)
        return evidence_id

    def load(self, evidence_id: str) -> dict[str, Any]:
        """Load evidence by id, failing closed on corruption."""
        return read_json_checked(self.evidence_dir / f"{evidence_id}.json")

    def evidence_for_candidate(self, candidate_id: str) -> list[dict[str, Any]]:
        """Return all evidence records bound to the given candidate id."""
        result: list[dict[str, Any]] = []
        for path in self.evidence_dir.glob("*.json"):
            record = read_json_checked(path)
            if record.get("candidate_id") == candidate_id:
                result.append(record)
        return result

    def is_fresh(self, evidence: dict[str, Any], candidate: Candidate) -> bool:
        """Return True only if the evidence is bound to the exact candidate."""
        return (
            evidence.get("candidate_id") == candidate.id
            and evidence.get("candidate_commit") == candidate.candidate_commit
        )


def evidence_is_fresh(evidence: Evidence, candidate: Candidate) -> bool:
    """Return True only if the evidence is bound to the exact candidate."""
    return (
        evidence.candidate.id == candidate.id
        and evidence.candidate.candidate_commit == candidate.candidate_commit
    )
