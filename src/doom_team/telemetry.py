"""Inference telemetry adapters."""

from __future__ import annotations

from doom_team.ports import InferenceTelemetryPort


class InMemoryTelemetry(InferenceTelemetryPort):
    """Record inference calls in memory for tests and small runs."""

    def __init__(self) -> None:
        self._records: list[dict[str, str]] = []

    def record(self, endpoint: str, model: str, runtime: str) -> None:
        """Record the actual provider/model/runtime route used."""
        self._records.append({"endpoint": endpoint, "model": model, "runtime": runtime})

    def last(self) -> dict[str, str] | None:
        """Return the most recent record, or None if none exist."""
        return self._records[-1] if self._records else None
