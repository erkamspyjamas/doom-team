"""Semantic recovery, blocker identity, and no-progress/duplicate detection.

Recovery is separate from runtime retries. Runtime failures (timeout, 429,
503, session crash) never consume the semantic budget. Only semantic blockers
(QA/Reviewer rejection, repeated acceptance failure, no-progress) consume
retries. The exhaustion state is persisted atomically and fails closed on
corruption so it survives a restart without ever being silently reset.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from doom_team.domain import Attempt, RecoveryEpoch
from doom_team.durable import atomic_write_json, read_json_checked


class SemanticBlockedError(Exception):
    """Raised when a candidate is semantically blocked."""

    def __init__(self, blocker: str) -> None:
        super().__init__(blocker)
        self.blocker = blocker


class SemanticExhaustedError(Exception):
    """Raised when the semantic retry budget is exhausted."""


@dataclass(frozen=True)
class Blocker:
    """Identity of a semantic blocker."""

    kind: str  # "qa", "reviewer", "no_progress", "duplicate", "acceptance"
    detail: str


@dataclass
class RecoveryState:
    """Mutable recovery state for a task attempt chain."""

    task_id: str
    semantic_retries_left: int
    blockers: dict[str, Blocker] = field(default_factory=dict)
    attempts: dict[str, Attempt] = field(default_factory=dict)
    evidence_digests: set[str] = field(default_factory=set)
    exhausted: bool = False


class RecoveryController:
    """Bounded semantic recovery with durable exhaustion.

    The controller persists its state to a JSON file with an embedded
    checksum. A corrupt or missing-checksum state file raises
    StateCorruptionError instead of silently resetting to defaults.
    Runtime retries are recorded separately and never decrement the semantic
    budget.
    """

    def __init__(self, state_path: str | Path, default_semantic_retries: int = 3) -> None:
        self._state_path = Path(state_path)
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self._default_semantic_retries = default_semantic_retries
        self._states: dict[str, RecoveryState] = {}
        self._load()

    def _load(self) -> None:
        if not self._state_path.exists():
            return
        data = read_json_checked(self._state_path)
        for task_id, raw in data.items():
            if not isinstance(raw, dict):
                continue
            blockers = {
                k: Blocker(**v)
                for k, v in raw.get("blockers", {}).items()
                if isinstance(v, dict)
            }
            attempts = {
                k: Attempt(**v)
                for k, v in raw.get("attempts", {}).items()
                if isinstance(v, dict)
            }
            self._states[task_id] = RecoveryState(
                task_id=task_id,
                semantic_retries_left=int(raw.get("semantic_retries_left", 0)),
                blockers=blockers,
                attempts=attempts,
                evidence_digests=set(raw.get("evidence_digests", [])),
                exhausted=bool(raw.get("exhausted", False)),
            )

    def _persist(self) -> None:
        payload: dict[str, Any] = {}
        for task_id, state in self._states.items():
            payload[task_id] = {
                "semantic_retries_left": state.semantic_retries_left,
                "blockers": {k: b.__dict__ for k, b in state.blockers.items()},
                "attempts": {k: a.__dict__ for k, a in state.attempts.items()},
                "evidence_digests": sorted(state.evidence_digests),
                "exhausted": state.exhausted,
            }
        atomic_write_json(self._state_path, payload)

    def initialize(self, task_id: str, semantic_retries: int) -> RecoveryEpoch:
        """Initialize recovery state for a task with a fixed semantic budget."""
        state = self._states.get(task_id)
        if state is None:
            state = RecoveryState(
                task_id=task_id,
                semantic_retries_left=semantic_retries,
                exhausted=semantic_retries <= 0,
            )
            self._states[task_id] = state
            self._persist()
        attempt = Attempt(id=f"{task_id}_a{len(state.attempts)}", task_id=task_id)
        state.attempts[attempt.id] = attempt
        return RecoveryEpoch(attempt=attempt, semantic_retries_left=state.semantic_retries_left)

    def state(self, task_id: str) -> RecoveryState | None:
        """Return the current recovery state for a task."""
        return self._states.get(task_id)

    def record_attempt(self, attempt: Attempt) -> None:
        """Record an attempt for blocker identity."""
        state = self._states.get(attempt.task_id)
        if state is not None:
            state.attempts[attempt.id] = attempt
            self._persist()

    def record_runtime_retry(self, attempt: Attempt) -> None:
        """Record a runtime retry. This never consumes the semantic budget."""
        self.record_attempt(attempt)

    def record_evidence(self, task_id: str, digest: str) -> None:
        """Record an evidence digest for duplicate detection."""
        state = self._states.get(task_id)
        if state is not None:
            state.evidence_digests.add(digest)
            self._persist()

    def is_duplicate_evidence(self, task_id: str, digest: str) -> bool:
        """Return True if identical evidence was already produced."""
        state = self._states.get(task_id)
        return state is not None and digest in state.evidence_digests

    def register_blocker(self, task_id: str, attempt: Attempt, blocker: Blocker) -> None:
        """Register a semantic blocker. The first blocker for an attempt is retained."""
        state = self._states.get(task_id)
        if state is None:
            return
        key = f"{attempt.id}:{blocker.kind}"
        if key not in state.blockers:
            state.blockers[key] = blocker
            self._persist()

    def is_blocked(self, task_id: str, attempt: Attempt, kind: str) -> bool:
        """Return True if this attempt already has the given blocker kind."""
        state = self._states.get(task_id)
        return state is not None and f"{attempt.id}:{kind}" in state.blockers

    def consume_semantic_retry(self, task_id: str) -> bool:
        """Consume one semantic retry. Returns True if budget remains after decrement."""
        state = self._states.get(task_id)
        if state is None:
            return False
        if state.semantic_retries_left <= 0:
            state.exhausted = True
            self._persist()
            return False
        state.semantic_retries_left -= 1
        if state.semantic_retries_left == 0:
            state.exhausted = True
        self._persist()
        return not state.exhausted

    def is_exhausted(self, task_id: str) -> bool:
        """Return True if the semantic budget is exhausted. Survives restart."""
        state = self._states.get(task_id)
        return state is not None and state.exhausted

    def on_semantic_blocker(
        self, task_id: str, attempt: Attempt, blocker: Blocker
    ) -> None:
        """Handle a semantic blocker: register and consume one retry."""
        was_blocked = self.is_blocked(task_id, attempt, blocker.kind)
        self.register_blocker(task_id, attempt, blocker)
        if was_blocked:
            # Already registered by a previous call for this attempt; do not double consume.
            return
        self.consume_semantic_retry(task_id)

    def next_attempt(self, task_id: str) -> Attempt | None:
        """Return a fresh attempt if retries remain, else None.

        If no state exists for the task yet (e.g., after a restore from a
        backup that predates any attempt), a fresh state is created using the
        default semantic retry budget.
        """
        state = self._states.get(task_id)
        if state is None:
            state = RecoveryState(
                task_id=task_id,
                semantic_retries_left=self._default_semantic_retries,
                exhausted=self._default_semantic_retries <= 0,
            )
            self._states[task_id] = state
            self._persist()
        if state.exhausted:
            return None
        attempt = Attempt(id=f"{task_id}_a{len(state.attempts)}", task_id=task_id)
        state.attempts[attempt.id] = attempt
        self._persist()
        return attempt
