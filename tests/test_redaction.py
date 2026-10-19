"""Tests for the pure event-value redaction helper."""

from __future__ import annotations

import ast
import datetime
import inspect
from pathlib import Path
from typing import Any

import pytest

import doom_team.observability as observability_module
import doom_team.redaction as redaction_module
from doom_team.observability import (
    DurableEventSink,
    Event,
    InMemoryEventSink,
    RedactingEventSink,
)
from doom_team.redaction import redact_value


def test_strings_replace_each_secret_in_supplied_order() -> None:
    assert redact_value("ab", ("a", "ab")) == "[REDACTED]b"
    assert redact_value("ab", ("ab", "a")) == "[REDACTED]"


def test_empty_secrets_are_ignored() -> None:
    assert redact_value("secret", ("",)) == "secret"
    assert redact_value("secret", ("", "secret", "")) == "[REDACTED]"


def test_duplicate_secrets_keep_repeated_replacement_behavior() -> None:
    assert redact_value("aaa", ("a", "a")) == "[REDACTED][REDACTED][REDACTED]"
    assert redact_value("aXa", ("a", "a")) == "[REDACTED]X[REDACTED]"


def test_literal_redacted_value_is_stable() -> None:
    assert redact_value("[REDACTED]", ("secret",)) == "[REDACTED]"


def test_no_secrets_leaves_text_unchanged() -> None:
    assert redact_value("plain", ()) == "plain"


def test_dict_keys_stay_unchanged_while_values_are_processed() -> None:
    source = {
        "secret": "secret",
        "nested": {"token": "secret"},
        "items": ["secret"],
    }
    result = redact_value(source, ("secret",))
    assert result == {
        "secret": "[REDACTED]",
        "nested": {"token": "[REDACTED]"},
        "items": ["[REDACTED]"],
    }
    assert list(result) == list(source)


def test_containers_preserve_builtin_types_and_ordering() -> None:
    result = redact_value({"items": ["s", ("s", 1)]}, ("s",))
    assert isinstance(result, dict)
    assert isinstance(result["items"], list)
    assert isinstance(result["items"][1], tuple)
    assert result == {"items": ["[REDACTED]", ("[REDACTED]", 1)]}


def test_non_container_non_string_values_pass_through() -> None:
    marker = object()
    for value in (None, True, False, 0, 42, 3.5, marker, b"secret"):
        assert redact_value(value, ("secret",)) is value


def test_input_is_never_mutated() -> None:
    inner_list = ["secret"]
    inner_dict = {"k": "secret"}
    source: dict[str, Any] = {
        "a": inner_list,
        "b": inner_dict,
        "c": ("secret",),
    }

    result = redact_value(source, ("secret",))

    assert source == {"a": ["secret"], "b": {"k": "secret"}, "c": ("secret",)}
    assert source["a"] is inner_list
    assert source["b"] is inner_dict
    assert result is not source
    assert result["a"] is not inner_list
    assert result["b"] is not inner_dict


def test_helper_signature() -> None:
    signature = inspect.signature(redact_value)
    assert list(signature.parameters) == ["value", "secret_values"]
    assert signature.return_annotation in {"Any", Any}


def test_module_imports_only_standard_library_typing_helpers() -> None:
    module_file = Path(redaction_module.__file__)
    tree = ast.parse(module_file.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0
            assert node.module is not None
            imported.add(node.module.split(".")[0])
    assert imported <= {"__future__", "typing"}


def test_sink_constructor_filters_empty_secrets_and_preserves_order() -> None:
    sink = RedactingEventSink(InMemoryEventSink(), ("", "a", "", "ab"))
    assert sink._secrets == ("a", "ab")
    assert sink._redact("ab") == "[REDACTED]b"

    reversed_sink = RedactingEventSink(InMemoryEventSink(), ("", "ab", "", "a"))
    assert reversed_sink._secrets == ("ab", "a")
    assert reversed_sink._redact("ab") == "[REDACTED]"


@pytest.mark.parametrize(
    ("value", "secrets", "expected"),
    [
        ("ab", ("a", "ab"), "[REDACTED]b"),
        ("ab", ("ab", "a"), "[REDACTED]"),
        ("aaa", ("a", "a"), "[REDACTED][REDACTED][REDACTED]"),
        ("aXa", ("a", "a"), "[REDACTED]X[REDACTED]"),
        ("secret", ("", "secret", ""), "[REDACTED]"),
        ("secret", ("",), "secret"),
        ("[REDACTED]", ("secret",), "[REDACTED]"),
    ],
)
def test_sink_redact_matches_helper_edge_cases(
    value: str,
    secrets: tuple[str, ...],
    expected: str,
) -> None:
    sink = RedactingEventSink(InMemoryEventSink(), secrets)
    assert sink._redact(value) == expected
    assert sink._redact(value) == redact_value(value, sink._secrets)


def test_sink_redact_preserves_dict_keys_and_container_types() -> None:
    source = {
        "secret": "secret",
        "nested": {"token": "secret"},
        "items": ["secret", ("secret", 1)],
    }
    sink = RedactingEventSink(InMemoryEventSink(), ("secret",))

    result = sink._redact(source)

    assert result == {
        "secret": "[REDACTED]",
        "nested": {"token": "[REDACTED]"},
        "items": ["[REDACTED]", ("[REDACTED]", 1)],
    }
    assert list(result) == list(source)
    assert isinstance(result, dict)
    assert isinstance(result["nested"], dict)
    assert isinstance(result["items"], list)
    assert isinstance(result["items"][1], tuple)


def test_sink_redact_passes_through_non_container_values() -> None:
    marker = object()
    sink = RedactingEventSink(InMemoryEventSink(), ("secret",))
    for value in (None, True, False, 0, 42, 3.5, marker, b"secret"):
        assert sink._redact(value) is value


def test_sink_redact_does_not_mutate_input() -> None:
    inner_list = ["secret"]
    inner_dict = {"k": "secret"}
    source: dict[str, Any] = {
        "a": inner_list,
        "b": inner_dict,
        "c": ("secret",),
    }
    sink = RedactingEventSink(InMemoryEventSink(), ("secret",))

    result = sink._redact(source)

    assert source == {"a": ["secret"], "b": {"k": "secret"}, "c": ("secret",)}
    assert source["a"] is inner_list
    assert source["b"] is inner_dict
    assert result is not source
    assert result["a"] is not inner_list
    assert result["b"] is not inner_dict


def test_sink_record_preserves_metadata_and_redacts_details() -> None:
    inner = InMemoryEventSink()
    sink = RedactingEventSink(inner, ("secret",))
    timestamp = datetime.datetime(2024, 1, 1, 12, 0, tzinfo=datetime.timezone.utc)
    original_details = {"message": "secret", "nested": {"key": "secret"}}
    event = Event(
        kind="TEST",
        details=original_details,
        producer_role="qa",
        producer_run="run-1",
        timestamp=timestamp,
    )

    sink.record(event)

    assert len(inner.events) == 1
    recorded = inner.events[0]
    assert recorded is not event
    assert recorded.kind == "TEST"
    assert recorded.producer_role == "qa"
    assert recorded.producer_run == "run-1"
    assert recorded.timestamp is timestamp
    assert recorded.details == {
        "message": "[REDACTED]",
        "nested": {"key": "[REDACTED]"},
    }
    assert original_details == {"message": "secret", "nested": {"key": "secret"}}
    assert event.details is original_details


def test_sink_redact_delegates_to_redaction_helper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[Any, tuple[str, ...]]] = []

    def fake_redact(value: Any, secret_values: tuple[str, ...]) -> Any:
        calls.append((value, secret_values))
        return "delegated"

    monkeypatch.setattr(observability_module, "redact_value", fake_redact)
    sink = RedactingEventSink(InMemoryEventSink(), ("", "secret", ""))

    assert sink._redact("value") == "delegated"
    assert calls == [("value", ("secret",))]


def test_sink_redact_body_has_no_local_recursive_traversal() -> None:
    source = inspect.getsource(RedactingEventSink._redact)
    assert "redact_value" in source
    assert "isinstance" not in source
    assert "replace(" not in source


def test_durable_sink_persists_redacted_jsonl_and_reloads(tmp_path: Path) -> None:
    sink = DurableEventSink(tmp_path, secret_values=("", "secret", ""))
    timestamp = datetime.datetime(2024, 1, 1, 12, 0, tzinfo=datetime.timezone.utc)
    sink.record(
        Event(
            kind="TEST",
            details={"message": "secret", "nested": ["secret"]},
            producer_role="qa",
            producer_run="run-1",
            timestamp=timestamp,
        )
    )

    log_path = tmp_path / "events" / "events.jsonl"
    raw = log_path.read_text(encoding="utf-8")
    assert "secret" not in raw
    assert "[REDACTED]" in raw

    reopened = DurableEventSink(tmp_path)
    events = reopened.load()
    assert len(events) == 1
    assert events[0].kind == "TEST"
    assert events[0].details == {
        "message": "[REDACTED]",
        "nested": ["[REDACTED]"],
    }
    assert events[0].producer_role == "qa"
    assert events[0].producer_run == "run-1"
    assert events[0].timestamp == timestamp
