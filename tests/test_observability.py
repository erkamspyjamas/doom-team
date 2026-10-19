"""Tests for durable observability: sinks, redaction, history, and health."""

import datetime
import json
from pathlib import Path

import pytest

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


def _lifecycle_event(
    kind: str,
    task_id: object = None,
    timestamp: datetime.datetime | None = None,
    *,
    include_task_id: bool = True,
) -> Event:
    details: dict[str, object] = {}
    if include_task_id:
        details["task_id"] = task_id
    if timestamp is None:
        timestamp = datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc)
    return Event(kind=kind, details=details, timestamp=timestamp)


def test_active_tasks_failure_then_retry_is_active(tmp_path: Path) -> None:
    sink = DurableEventSink(tmp_path)
    first = datetime.datetime(2024, 1, 1, 12, 0, tzinfo=datetime.timezone.utc)
    retry = datetime.datetime(2024, 1, 1, 12, 1, tzinfo=datetime.timezone.utc)
    sink.record(_lifecycle_event("TASK_STARTED", "t1", first))
    sink.record(_lifecycle_event("TASK_FAILED", "t1", first))
    sink.record(_lifecycle_event("TASK_STARTED", "t1", retry))

    assert EventHistory(sink).active_tasks() == [
        {"task_id": "t1", "started_at": retry.isoformat()}
    ]


def test_active_tasks_completed_then_restarted_is_active(tmp_path: Path) -> None:
    sink = DurableEventSink(tmp_path)
    first = datetime.datetime(2024, 1, 1, 12, 0, tzinfo=datetime.timezone.utc)
    restarted = datetime.datetime(2024, 1, 1, 12, 2, tzinfo=datetime.timezone.utc)
    sink.record(_lifecycle_event("TASK_STARTED", "t1", first))
    sink.record(_lifecycle_event("TASK_COMPLETED", "t1", first))
    sink.record(_lifecycle_event("TASK_STARTED", "t1", restarted))

    assert EventHistory(sink).active_tasks() == [
        {"task_id": "t1", "started_at": restarted.isoformat()}
    ]


def test_active_tasks_terminal_after_retry_is_inactive(tmp_path: Path) -> None:
    sink = DurableEventSink(tmp_path)
    first = datetime.datetime(2024, 1, 1, 12, 0, tzinfo=datetime.timezone.utc)
    second = datetime.datetime(2024, 1, 1, 12, 1, tzinfo=datetime.timezone.utc)
    sink.record(_lifecycle_event("TASK_STARTED", "t1", first))
    sink.record(_lifecycle_event("TASK_FAILED", "t1", first))
    sink.record(_lifecycle_event("TASK_STARTED", "t1", second))
    sink.record(_lifecycle_event("TASK_FAILED", "t1", second))

    assert EventHistory(sink).active_tasks() == []


def test_active_tasks_repeated_starts_update_started_at_without_duplicates(
    tmp_path: Path,
) -> None:
    sink = DurableEventSink(tmp_path)
    first = datetime.datetime(2024, 1, 1, 12, 0, tzinfo=datetime.timezone.utc)
    second = datetime.datetime(2024, 1, 1, 12, 1, tzinfo=datetime.timezone.utc)
    third = datetime.datetime(2024, 1, 1, 12, 2, tzinfo=datetime.timezone.utc)
    sink.record(_lifecycle_event("TASK_STARTED", "t1", first))
    sink.record(_lifecycle_event("TASK_STARTED", "t1", second))
    sink.record(_lifecycle_event("TASK_STARTED", "t1", third))

    assert EventHistory(sink).active_tasks() == [
        {"task_id": "t1", "started_at": third.isoformat()}
    ]


def test_active_tasks_interleaved_task_ids_report_independent_state(
    tmp_path: Path,
) -> None:
    sink = DurableEventSink(tmp_path)
    t1_first = datetime.datetime(2024, 1, 1, 12, 0, tzinfo=datetime.timezone.utc)
    t2_start = datetime.datetime(2024, 1, 1, 12, 1, tzinfo=datetime.timezone.utc)
    t1_retry = datetime.datetime(2024, 1, 1, 12, 2, tzinfo=datetime.timezone.utc)
    sink.record(_lifecycle_event("TASK_STARTED", "t1", t1_first))
    sink.record(_lifecycle_event("TASK_STARTED", "t2", t2_start))
    sink.record(_lifecycle_event("TASK_FAILED", "t1", t2_start))
    sink.record(_lifecycle_event("TASK_STARTED", "t1", t1_retry))
    sink.record(_lifecycle_event("TASK_COMPLETED", "t2", t1_retry))

    assert EventHistory(sink).active_tasks() == [
        {"task_id": "t1", "started_at": t1_retry.isoformat()}
    ]


def test_active_tasks_ignore_invalid_ids_and_unrelated_events(tmp_path: Path) -> None:
    sink = DurableEventSink(tmp_path)
    sink.record(Event(kind="TASK_STARTED", details={}))
    sink.record(Event(kind="TASK_STARTED", details={"task_id": ""}))
    sink.record(Event(kind="TASK_STARTED", details={"task_id": 123}))
    sink.record(Event(kind="TASK_STARTED", details={"task_id": None}))
    sink.record(Event(kind="TASK_COMPLETED", details={"task_id": "ghost"}))
    sink.record(Event(kind="UNRELATED", details={"task_id": "ignored"}))
    valid = datetime.datetime(2024, 1, 1, 12, 0, tzinfo=datetime.timezone.utc)
    sink.record(_lifecycle_event("TASK_STARTED", "valid", valid))

    assert EventHistory(sink).active_tasks() == [
        {"task_id": "valid", "started_at": valid.isoformat()}
    ]


def test_active_tasks_use_append_order_when_timestamps_move_backward(
    tmp_path: Path,
) -> None:
    sink = DurableEventSink(tmp_path)
    later = datetime.datetime(2024, 1, 1, 12, 5, tzinfo=datetime.timezone.utc)
    earlier = datetime.datetime(2024, 1, 1, 12, 0, tzinfo=datetime.timezone.utc)
    sink.record(_lifecycle_event("TASK_STARTED", "t1", later))
    sink.record(_lifecycle_event("TASK_FAILED", "t1", later))
    sink.record(_lifecycle_event("TASK_STARTED", "t1", earlier))

    assert EventHistory(sink).active_tasks() == [
        {"task_id": "t1", "started_at": earlier.isoformat()}
    ]


def test_active_tasks_use_append_order_when_timestamps_tie(tmp_path: Path) -> None:
    sink = DurableEventSink(tmp_path)
    same = datetime.datetime(2024, 1, 1, 12, 0, tzinfo=datetime.timezone.utc)
    sink.record(_lifecycle_event("TASK_STARTED", "t1", same))
    sink.record(_lifecycle_event("TASK_COMPLETED", "t1", same))
    sink.record(_lifecycle_event("TASK_STARTED", "t1", same))

    assert EventHistory(sink).active_tasks() == [
        {"task_id": "t1", "started_at": same.isoformat()}
    ]


def test_active_tasks_terminal_before_start_does_not_block_later_start(
    tmp_path: Path,
) -> None:
    sink = DurableEventSink(tmp_path)
    t1_start = datetime.datetime(2024, 1, 1, 12, 0, tzinfo=datetime.timezone.utc)
    t2_start = datetime.datetime(2024, 1, 1, 12, 1, tzinfo=datetime.timezone.utc)
    sink.record(_lifecycle_event("TASK_FAILED", "t1", t1_start))
    sink.record(_lifecycle_event("TASK_COMPLETED", "t2", t1_start))
    sink.record(_lifecycle_event("TASK_STARTED", "t1", t1_start))
    sink.record(_lifecycle_event("TASK_STARTED", "t2", t2_start))

    assert EventHistory(sink).active_tasks() == [
        {"task_id": "t1", "started_at": t1_start.isoformat()},
        {"task_id": "t2", "started_at": t2_start.isoformat()},
    ]


def test_active_tasks_reading_does_not_rewrite_persisted_events(tmp_path: Path) -> None:
    sink = DurableEventSink(tmp_path)
    sink.record(_lifecycle_event("TASK_STARTED", "t1"))
    log_path = tmp_path / "events" / "events.jsonl"
    before = log_path.read_bytes()

    EventHistory(sink).active_tasks()

    assert log_path.read_bytes() == before


def _query_event(
    kind: str,
    *,
    task_id: object = None,
    producer_role: str = "system",
    timestamp: datetime.datetime | None = None,
    include_task_id: bool = True,
) -> Event:
    details: dict[str, object] = {}
    if include_task_id:
        details["task_id"] = task_id
    if timestamp is None:
        timestamp = datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc)
    return Event(
        kind=kind,
        details=details,
        producer_role=producer_role,
        timestamp=timestamp,
    )


def test_query_default_limit_is_ten(tmp_path: Path) -> None:
    sink = DurableEventSink(tmp_path)
    for index in range(12):
        sink.record(_query_event("MARK", task_id=str(index)))

    result = EventHistory(sink).query(kinds=["MARK"])

    assert [event.details["task_id"] for event in result] == [
        str(index) for index in range(2, 12)
    ]


def test_query_filters_kinds_exactly_and_deduplicates(tmp_path: Path) -> None:
    sink = DurableEventSink(tmp_path)
    sink.record(_query_event("TASK_STARTED", task_id="t1"))
    sink.record(_query_event("task_started", task_id="t2"))
    sink.record(_query_event("PROVIDER_ERROR", include_task_id=False))
    history = EventHistory(sink)

    assert [event.kind for event in history.query(kinds=["TASK_STARTED"])] == [
        "TASK_STARTED"
    ]
    assert history.query(kinds=[]) == []
    assert [
        event.kind
        for event in history.query(kinds=("PROVIDER_ERROR", "PROVIDER_ERROR"))
    ] == ["PROVIDER_ERROR"]


def test_query_task_id_matches_exact_nonempty_string_details(tmp_path: Path) -> None:
    sink = DurableEventSink(tmp_path)
    sink.record(_query_event("A", task_id="t1"))
    sink.record(_query_event("B", task_id="T1"))
    sink.record(_query_event("C", task_id=" t1 "))
    sink.record(_query_event("D", task_id=123))
    sink.record(_query_event("E", task_id=None))
    sink.record(_query_event("F", include_task_id=False))
    history = EventHistory(sink)

    assert [event.kind for event in history.query(task_id="t1")] == ["A"]
    assert [event.kind for event in history.query(task_id=" t1 ")] == ["C"]


def test_query_filters_producer_role_exactly(tmp_path: Path) -> None:
    sink = DurableEventSink(tmp_path)
    sink.record(_query_event("A", producer_role="qa"))
    sink.record(_query_event("B", producer_role="QA"))
    sink.record(_query_event("C", producer_role="qa "))
    history = EventHistory(sink)

    assert [event.kind for event in history.query(producer_role="qa")] == ["A"]
    assert [event.kind for event in history.query(producer_role="qa ")] == ["C"]


def test_query_composes_filters_conjunctively(tmp_path: Path) -> None:
    sink = DurableEventSink(tmp_path)
    sink.record(_query_event("TASK_STARTED", task_id="t1", producer_role="dev"))
    sink.record(_query_event("TASK_STARTED", task_id="t1", producer_role="qa"))
    sink.record(_query_event("TASK_FAILED", task_id="t1", producer_role="dev"))
    sink.record(_query_event("TASK_STARTED", task_id="t2", producer_role="dev"))
    sink.record(_query_event("TASK_FAILED", task_id="t2", producer_role="qa"))
    history = EventHistory(sink)

    result = history.query(
        kinds=("TASK_STARTED", "TASK_FAILED"),
        task_id="t1",
        producer_role="dev",
    )

    assert [(event.kind, event.producer_role) for event in result] == [
        ("TASK_STARTED", "dev"),
        ("TASK_FAILED", "dev"),
    ]


def test_query_applies_limit_after_matching(tmp_path: Path) -> None:
    sink = DurableEventSink(tmp_path)
    sink.record(_query_event("MATCH", task_id="m1"))
    sink.record(_query_event("OTHER", task_id="n1"))
    sink.record(_query_event("MATCH", task_id="m2"))
    sink.record(_query_event("OTHER", task_id="n2"))
    sink.record(_query_event("MATCH", task_id="m3"))
    sink.record(_query_event("OTHER", task_id="n3"))
    history = EventHistory(sink)

    assert [
        event.details["task_id"] for event in history.query(kinds=["MATCH"], limit=2)
    ] == ["m2", "m3"]
    assert [
        event.details["task_id"] for event in history.query(kinds=["MATCH"], limit=99)
    ] == ["m1", "m2", "m3"]
    assert history.query(kinds=["MATCH"], limit=0) == []


def test_query_preserves_append_order_not_timestamp_order(tmp_path: Path) -> None:
    sink = DurableEventSink(tmp_path)
    late = datetime.datetime(2024, 1, 1, 12, 5, tzinfo=datetime.timezone.utc)
    early = datetime.datetime(2024, 1, 1, 12, 0, tzinfo=datetime.timezone.utc)
    sink.record(_query_event("MARK", task_id="first", timestamp=late))
    sink.record(_query_event("MARK", task_id="second", timestamp=early))
    sink.record(_query_event("MARK", task_id="third", timestamp=late))
    history = EventHistory(sink)

    assert [
        event.details["task_id"] for event in history.query(kinds=["MARK"])
    ] == ["first", "second", "third"]
    assert [
        event.details["task_id"] for event in history.query(kinds=["MARK"], limit=2)
    ] == ["second", "third"]


@pytest.mark.parametrize("bad_limit", [True, False, 1.5, "10", None, [], {}])
def test_query_rejects_invalid_limit_types(
    tmp_path: Path, bad_limit: object
) -> None:
    history = EventHistory(DurableEventSink(tmp_path))
    with pytest.raises(TypeError):
        history.query(limit=bad_limit)


@pytest.mark.parametrize("bad_limit", [-1, -10])
def test_query_rejects_negative_limits(tmp_path: Path, bad_limit: int) -> None:
    history = EventHistory(DurableEventSink(tmp_path))
    with pytest.raises(ValueError):
        history.query(limit=bad_limit)


@pytest.mark.parametrize(
    "bad_kinds",
    [
        "TASK_STARTED",
        b"TASK_STARTED",
        {"TASK_STARTED"},
        {"TASK_STARTED": True},
        1,
        1.0,
    ],
)
def test_query_rejects_invalid_kinds_container_types(
    tmp_path: Path, bad_kinds: object
) -> None:
    history = EventHistory(DurableEventSink(tmp_path))
    with pytest.raises(TypeError):
        history.query(kinds=bad_kinds)


def test_query_rejects_invalid_kind_entries(tmp_path: Path) -> None:
    history = EventHistory(DurableEventSink(tmp_path))
    with pytest.raises(TypeError):
        history.query(kinds=["TASK_STARTED", 1])
    with pytest.raises(TypeError):
        history.query(kinds=("TASK_STARTED", None))
    with pytest.raises(TypeError):
        history.query(kinds=[True])
    with pytest.raises(ValueError):
        history.query(kinds=[""])
    with pytest.raises(ValueError):
        history.query(kinds=("TASK_STARTED", ""))


@pytest.mark.parametrize("field", ["task_id", "producer_role"])
@pytest.mark.parametrize("bad", [1, b"x", [], {}, True])
def test_query_rejects_invalid_string_filter_types(
    tmp_path: Path, field: str, bad: object
) -> None:
    history = EventHistory(DurableEventSink(tmp_path))
    with pytest.raises(TypeError):
        history.query(**{field: bad})


@pytest.mark.parametrize("field", ["task_id", "producer_role"])
def test_query_rejects_empty_string_filters(tmp_path: Path, field: str) -> None:
    history = EventHistory(DurableEventSink(tmp_path))
    with pytest.raises(ValueError):
        history.query(**{field: ""})


def test_query_validates_arguments_even_with_zero_limit(tmp_path: Path) -> None:
    history = EventHistory(DurableEventSink(tmp_path))
    with pytest.raises(TypeError):
        history.query(limit=0, kinds="TASK_STARTED")
    with pytest.raises(ValueError):
        history.query(limit=0, kinds=[""])
    with pytest.raises(ValueError):
        history.query(limit=0, task_id="")
    with pytest.raises(ValueError):
        history.query(limit=0, producer_role="")


def test_query_validates_arguments_on_empty_log(tmp_path: Path) -> None:
    history = EventHistory(DurableEventSink(tmp_path / "empty"))

    assert history.query() == []
    with pytest.raises(TypeError):
        history.query(kinds={"TASK_STARTED"})
    with pytest.raises(ValueError):
        history.query(kinds=[""])
    with pytest.raises(ValueError):
        history.query(task_id="")


def test_query_is_read_only_and_repeated_calls_are_identical(
    tmp_path: Path,
) -> None:
    sink = DurableEventSink(tmp_path)
    sink.record(_query_event("TASK_STARTED", task_id="t1", producer_role="dev"))
    sink.record(_query_event("TASK_FAILED", task_id="t1", producer_role="dev"))
    sink.record(_query_event("TASK_STARTED", task_id="t2", producer_role="dev"))
    log_path = tmp_path / "events" / "events.jsonl"
    before = log_path.read_bytes()
    history = EventHistory(sink)

    first = history.query(kinds=("TASK_STARTED",), limit=2)
    second = history.query(kinds=("TASK_STARTED",), limit=2)

    assert first == second
    assert [event.details["task_id"] for event in first] == ["t1", "t2"]
    assert log_path.read_bytes() == before
    with pytest.raises(ValueError):
        history.query(limit=-1)
    assert log_path.read_bytes() == before


def test_query_results_survive_durable_reopen_without_rewriting_log(
    tmp_path: Path,
) -> None:
    sink = DurableEventSink(tmp_path)
    sink.record(_query_event("TASK_STARTED", task_id="t1", producer_role="dev"))
    sink.record(_query_event("TASK_FAILED", task_id="t1", producer_role="dev"))
    sink.record(_query_event("TASK_STARTED", task_id="t2", producer_role="dev"))
    log_path = tmp_path / "events" / "events.jsonl"
    before = log_path.read_bytes()

    first = EventHistory(sink).query(
        kinds=("TASK_STARTED", "TASK_FAILED"),
        task_id="t1",
        producer_role="dev",
        limit=1,
    )
    reopened = EventHistory(DurableEventSink(tmp_path)).query(
        kinds=("TASK_STARTED", "TASK_FAILED"),
        task_id="t1",
        producer_role="dev",
        limit=1,
    )

    assert [event.kind for event in first] == ["TASK_FAILED"]
    assert [event.to_dict() for event in reopened] == [
        event.to_dict() for event in first
    ]
    assert log_path.read_bytes() == before
