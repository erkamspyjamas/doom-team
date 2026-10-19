"""Durable observability: structured event history with redaction.

Provides an append-only event log, in-memory and durable sinks, health and
readiness helpers, and durable provider-route telemetry. Secrets are
redacted at the sink before persistence; private reasoning contents are
never recorded by the event producers.
"""

from __future__ import annotations

import datetime
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from doom_team.ports import InferenceTelemetryPort


@dataclass(frozen=True)
class Event:
    """A structured, durable observability event."""

    kind: str
    details: dict[str, Any]
    producer_role: str = "system"
    producer_run: str = ""
    timestamp: datetime.datetime = field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc)
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "kind": self.kind,
            "producer_role": self.producer_role,
            "producer_run": self.producer_run,
            "details": self.details,
        }


@runtime_checkable
class EventSink(Protocol):
    def record(self, event: Event) -> None:
        """Persist an event durably (or in memory)."""


class InMemoryEventSink:
    """In-memory event sink for tests and small runs."""

    def __init__(self) -> None:
        self.events: list[Event] = []

    def record(self, event: Event) -> None:
        self.events.append(event)

    def load(self) -> list[Event]:
        return list(self.events)


class RedactingEventSink:
    """Wraps another sink, redacting secret values before recording."""

    def __init__(self, sink: EventSink, secret_values: tuple[str, ...] = ()) -> None:
        self._sink = sink
        self._secrets = tuple(s for s in secret_values if s)

    def _redact(self, value: Any) -> Any:
        if isinstance(value, str):
            redacted = value
            for secret in self._secrets:
                if secret and secret in redacted:
                    redacted = redacted.replace(secret, "[REDACTED]")
            return redacted
        if isinstance(value, dict):
            return {k: self._redact(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._redact(v) for v in value]
        if isinstance(value, tuple):
            return tuple(self._redact(v) for v in value)
        return value

    def record(self, event: Event) -> None:
        redacted = Event(
            kind=event.kind,
            details=self._redact(event.details),
            producer_role=event.producer_role,
            producer_run=event.producer_run,
            timestamp=event.timestamp,
        )
        self._sink.record(redacted)


class _FileSink:
    """Append-only file sink used internally by DurableEventSink."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def record(self, event: Event) -> None:
        with open(self._path, "a") as f:
            f.write(json.dumps(event.to_dict(), default=str) + "\n")


class DurableEventSink(RedactingEventSink):
    """Append-only JSON-lines event sink with redaction and restart survival."""

    def __init__(
        self,
        base_dir: str | Path,
        secret_values: tuple[str, ...] = (),
    ) -> None:
        self.base_dir = Path(base_dir)
        self.events_dir = self.base_dir / "events"
        self.events_dir.mkdir(parents=True, exist_ok=True)
        self._path = self.events_dir / "events.jsonl"
        inner = _FileSink(self._path)
        super().__init__(inner, secret_values)

    def load(self) -> list[Event]:
        if not self._path.exists():
            return []
        events: list[Event] = []
        with open(self._path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(data, dict):
                    continue
                events.append(_event_from_dict(data))
        return events


class DurableTelemetry(InferenceTelemetryPort):
    """Records actual provider/model/runtime route to the durable event sink."""

    def __init__(
        self,
        sink: DurableEventSink,
        producer_role: str = "inference",
    ) -> None:
        self._sink = sink
        self._producer_role = producer_role

    def record(self, endpoint: str, model: str, runtime: str) -> None:
        self._sink.record(
            Event(
                kind="PROVIDER_ROUTE",
                details={"endpoint": endpoint, "model": model, "runtime": runtime},
                producer_role=self._producer_role,
            )
        )


class EventHistory:
    """Reads the durable event log and provides operator diagnostics."""

    def __init__(self, sink: DurableEventSink) -> None:
        self._sink = sink

    def recent(self, limit: int = 10) -> list[Event]:
        events = self._sink.load()
        return events[-limit:]

    def active_tasks(self) -> list[dict[str, str]]:
        """Return tasks currently active according to durable append order.

        A task becomes active on its latest ``TASK_STARTED`` event and
        inactive on a later ``TASK_COMPLETED`` or ``TASK_FAILED``. Repeated
        starts update ``started_at`` without duplicating the task; terminal
        events without a preceding start do not block a future start.
        Invalid or unrelated events are ignored.
        """
        events = self._sink.load()
        started_at: dict[str, str] = {}
        active: set[str] = set()
        for event in events:
            task_id = event.details.get("task_id")
            if not isinstance(task_id, str) or not task_id:
                continue
            if event.kind == "TASK_STARTED":
                started_at[task_id] = event.timestamp.isoformat()
                active.add(task_id)
            elif event.kind in ("TASK_COMPLETED", "TASK_FAILED"):
                active.discard(task_id)
        return [
            {"task_id": tid, "started_at": ts}
            for tid, ts in started_at.items()
            if tid in active
        ]

    def recent_errors(self, limit: int = 10) -> list[Event]:
        events = self._sink.load()
        return [
            e
            for e in events
            if e.kind in ("TASK_FAILED", "PROVIDER_ERROR", "APPROVAL_ERROR")
        ][-limit:]

    def last_events(self, limit: int = 10) -> list[Event]:
        return self.recent(limit)


def _event_from_dict(data: dict[str, Any]) -> Event:
    try:
        ts = datetime.datetime.fromisoformat(data["timestamp"])
    except (KeyError, ValueError):
        ts = datetime.datetime.now(datetime.timezone.utc)
    return Event(
        kind=data.get("kind", "UNKNOWN"),
        details=data.get("details", {}) if isinstance(data.get("details"), dict) else {},
        producer_role=data.get("producer_role", "system"),
        producer_run=data.get("producer_run", ""),
        timestamp=ts,
    )


@runtime_checkable
class HealthCheckable(Protocol):
    @property
    def state_dir(self) -> Path: ...

    @property
    def model_provider(self) -> str: ...

    @property
    def model_name(self) -> str: ...

    @property
    def provider_api_key_env(self) -> str: ...


def check_health(settings: HealthCheckable) -> tuple[bool, str]:
    """Return (healthy, message). Healthy means durable state is reachable."""
    try:
        state_dir = settings.state_dir
        ev_dir = state_dir / "evidence"
        verdict_dir = state_dir / "verdicts"
        events_dir = state_dir / "events"
        for d in (ev_dir, verdict_dir, events_dir):
            if not d.is_dir():
                return False, f"missing directory {d}"
        sink = DurableEventSink(state_dir)
        sink.load()
        return True, "healthy"
    except Exception as exc:
        return False, f"unhealthy: {exc}"


def check_readiness(settings: HealthCheckable) -> tuple[bool, str]:
    """Return (ready, message). Ready means config is valid and state usable."""
    healthy, msg = check_health(settings)
    if not healthy:
        return False, msg
    if not settings.model_provider or not settings.model_name:
        return False, "model provider/name missing"
    if not settings.provider_api_key_env:
        return False, "credential env missing"
    return True, "ready"
