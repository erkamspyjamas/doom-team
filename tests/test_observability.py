"""Tests for durable observability: sinks, redaction, history, and health."""

import json
from pathlib import Path

from doom_team.observability import (
    DurableEventSink,
    DurableTelemetry,
    Event,
    EventHistory,
    InMemoryEventSink,
    check_health,
    check_readiness,
)


def test_in_memory_sink_records_events() -> None:
    sink = InMemoryEventSink()
    sink.record(Event(kind="TEST", details={"a": 1}))
    assert len(sink.load()) == 1
    assert sink.load()[0].kind == "TEST"


def test_durable_sink_persists_and_survives_restart(tmp_path: Path) -> None:
    sink = DurableEventSink(tmp_path)
    sink.record(Event(kind="TEST", details={"a": "b"}, producer_role="qa"))

    # Simulate restart
    sink2 = DurableEventSink(tmp_path)
    events = sink2.load()
    assert len(events) == 1
    assert events[0].kind == "TEST"
    assert events[0].details == {"a": "b"}
    assert events[0].producer_role == "qa"


def test_durable_sink_redacts_secret_values(tmp_path: Path) -> None:
    sink = DurableEventSink(tmp_path, secret_values=("super-secret",))
    sink.record(Event(kind="TEST", details={"message": "leak super-secret here"}))
    events = sink.load()
    assert "super-secret" not in json.dumps(events[0].to_dict())
    assert "[REDACTED]" in events[0].details["message"]


def test_durable_sink_redacts_nested_secrets(tmp_path: Path) -> None:
    sink = DurableEventSink(tmp_path, secret_values=("hunter2",))
    sink.record(
        Event(
            kind="TEST",
            details={"nested": {"password": "hunter2", "list": ["hunter2", "ok"]}},
        )
    )
    events = sink.load()
    raw = json.dumps(events[0].to_dict())
    assert "hunter2" not in raw
    assert "[REDACTED]" in raw


def test_event_history_tracks_active_tasks(tmp_path: Path) -> None:
    sink = DurableEventSink(tmp_path)
    sink.record(Event(kind="TASK_STARTED", details={"task_id": "t1"}))
    sink.record(Event(kind="TASK_STARTED", details={"task_id": "t2"}))
    sink.record(Event(kind="TASK_COMPLETED", details={"task_id": "t1"}))
    history = EventHistory(sink)
    active = history.active_tasks()
    assert len(active) == 1
    assert active[0]["task_id"] == "t2"


def test_event_history_recent_errors(tmp_path: Path) -> None:
    sink = DurableEventSink(tmp_path)
    sink.record(Event(kind="TASK_COMPLETED", details={"task_id": "t1"}))
    sink.record(Event(kind="PROVIDER_ERROR", details={"kind": "timeout"}))
    history = EventHistory(sink)
    errors = history.recent_errors()
    assert len(errors) == 1
    assert errors[0].kind == "PROVIDER_ERROR"


def test_durable_telemetry_records_route(tmp_path: Path) -> None:
    sink = DurableEventSink(tmp_path)
    telemetry = DurableTelemetry(sink)
    telemetry.record("endpoint", "deepseek-v4", "local")
    events = sink.load()
    assert events[0].kind == "PROVIDER_ROUTE"
    assert events[0].details == {"endpoint": "endpoint", "model": "deepseek-v4", "runtime": "local"}


def test_health_and_readiness_checks(tmp_path: Path) -> None:
    class FakeSettings:
        state_dir = tmp_path
        model_provider = "nvidia"
        model_name = "deepseek-v4"
        provider_api_key_env = "DOOM_API_KEY"

    # Create required dirs
    (tmp_path / "evidence").mkdir()
    (tmp_path / "verdicts").mkdir()
    (tmp_path / "events").mkdir()

    healthy, msg = check_health(FakeSettings())
    assert healthy
    ready, _ = check_readiness(FakeSettings())
    assert ready


def test_health_fails_when_dir_missing(tmp_path: Path) -> None:
    class FakeSettings:
        state_dir = tmp_path
        model_provider = "nvidia"
        model_name = "deepseek-v4"
        provider_api_key_env = "DOOM_API_KEY"

    (tmp_path / "evidence").mkdir()
    (tmp_path / "verdicts").mkdir()
    healthy, _ = check_health(FakeSettings())
    assert not healthy


def test_provider_error_redacted_at_sink(tmp_path: Path) -> None:
    from doom_team.inference import InferenceError, OpenAICompatibleInferenceClient
    from tests.test_inference import make_fake_urlopen

    sink = DurableEventSink(tmp_path, secret_values=("provider-secret",))
    state = {"behavior": "overload"}
    client = OpenAICompatibleInferenceClient(
        base_url="http://fake.invalid",
        api_key="provider-secret",
        model="test-model",
        urlopen=make_fake_urlopen(state),
        event_sink=sink,
    )
    try:
        client.complete([{"role": "user", "content": "hi"}])
    except InferenceError:
        pass
    events = sink.load()
    assert len(events) == 1
    assert events[0].kind == "PROVIDER_ERROR"
    assert "provider-secret" not in json.dumps(events[0].to_dict())
