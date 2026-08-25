"""Port interfaces for Doom Team infrastructure.

These protocols define the boundary between the domain core and
replaceable infrastructure adapters (orchestration, workspace,
repository, evidence, telemetry, approval, clock). The core must not
leak engine-specific types; adapters implement these protocols.
"""

from __future__ import annotations

import datetime
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    def utcnow(self) -> datetime.datetime:
        """Return the current UTC time."""


@runtime_checkable
class WorkspacePort(Protocol):
    def create_workspace(self, task_id: str) -> str:
        """Create an isolated workspace for a task and return its id."""

    def snapshot(self, workspace_id: str) -> str:
        """Capture a reversible snapshot of the workspace."""

    def restore(self, workspace_id: str, snapshot_id: str) -> None:
        """Restore a workspace to a previous snapshot."""

    def destroy(self, workspace_id: str) -> None:
        """Destroy a workspace, preserving failed/dirty state per policy."""

    def apply_patch(self, workspace_id: str, patch: bytes) -> None:
        """Apply an exact-file patch to the workspace."""


@runtime_checkable
class RepositoryPort(Protocol):
    def baseline_commit(self, workspace_id: str) -> str:
        """Return the baseline commit hash for a workspace."""

    def candidate_commit(self, workspace_id: str) -> str:
        """Return the candidate commit hash produced in the workspace."""

    def commit(self, workspace_id: str, message: str) -> str:
        """Commit the current workspace state and return the new hash."""

    def escape_check(self, workspace_id: str) -> bool:
        """Return True if the workspace has escaped its isolation."""


@runtime_checkable
class EvidenceStore(Protocol):
    def save(self, evidence: dict[str, Any]) -> str:
        """Persist evidence and return its id."""

    def load(self, evidence_id: str) -> dict[str, Any]:
        """Load evidence by id."""


@runtime_checkable
class InferenceTelemetryPort(Protocol):
    def record(self, endpoint: str, model: str, runtime: str) -> None:
        """Record actual provider/model/runtime used for an inference."""


@runtime_checkable
class ApprovalPort(Protocol):
    def request_approval(self, action: str, context: dict[str, Any]) -> bool:
        """Request human approval for an irreversible action."""


@runtime_checkable
class OrchestrationPort(Protocol):
    def enqueue(self, task: Any, dependencies: tuple[str, ...] = ()) -> str:
        """Enqueue a durable unit of work and return its id."""

    def observe(self, run_id: str) -> Any:
        """Observe the current state of a run."""

    def is_runtime_failure(self, run_id: str) -> bool:
        """Determine whether a run failure is a transient runtime issue."""

    def recover(self, run_id: str) -> None:
        """Trigger runtime recovery without touching semantic history."""


@runtime_checkable
class InferencePort(Protocol):
    def complete(self, messages: list[dict[str, str]]) -> Any:
        """Complete a chat conversation and return the normalized response."""
