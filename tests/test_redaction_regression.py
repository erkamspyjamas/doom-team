"""Additive regression tests for the extracted event-value redaction helper.

These tests pin the pre-refactor ``RedactingEventSink._redact`` behavior that
the extraction must preserve: a string is rewritten only when a nonempty
secret actually occurs in it, so a no-match ``str`` subclass keeps its type
and identity, while an actual match still yields the plain ``str`` returned by
``str.replace``.
"""

from __future__ import annotations

from typing import Any

import pytest

from doom_team.observability import Event, InMemoryEventSink, RedactingEventSink
from doom_team.redaction import REDACTED, redact_value


class Text(str):
    """``str`` subclass used to observe identity and type preservation."""


class ExplodingText(str):
    """``str`` subclass that fails loudly if replacement is invoked."""

    def replace(self, *args: Any, **kwargs: Any) -> str:  # pragma: no cover - guard
        raise AssertionError("replace() must not run when the secret is absent")


def test_no_match_returns_same_subclass_instance() -> None:
    value = Text("visible content")

    result = redact_value(value, ("absent",))

    assert result is value
    assert type(result) is Text
    assert result == "visible content"


@pytest.mark.parametrize(
    "secrets",
    [(), ("",), ("", "absent", ""), ("absent", "missing"), ("",)],
    ids=("no-secrets", "empty-secret", "empty-and-absent", "absent", "empty-again"),
)
def test_no_match_ignores_empty_and_absent_secrets(
    secrets: tuple[str, ...],
) -> None:
    value = Text("visible content")

    assert redact_value(value, secrets) is value


def test_no_match_plain_string_is_unchanged() -> None:
    assert redact_value("visible content", ("absent",)) == "visible content"
    assert redact_value("visible content", ("",)) == "visible content"
    assert redact_value("visible content", ()) == "visible content"


def test_replacement_is_not_invoked_when_secret_is_absent() -> None:
    value = ExplodingText("visible content")

    assert redact_value(value, ("", "absent", "")) is value


def test_match_on_subclass_returns_plain_str() -> None:
    result = redact_value(Text("visible secret"), ("secret",))

    assert result == "visible [REDACTED]"
    assert type(result) is str
    assert not isinstance(result, Text)


def test_match_producing_identical_text_still_returns_plain_str() -> None:
    result = redact_value(Text(REDACTED), (REDACTED,))

    assert result == REDACTED
    assert type(result) is str


def test_empty_string_stays_empty() -> None:
    for secrets in ((), ("",), ("secret",), ("", "secret", "")):
        result = redact_value("", secrets)
        assert result == ""
        assert isinstance(result, str)


@pytest.mark.parametrize(
    ("text", "secrets", "expected"),
    [
        ("ab", ("a", "ab"), "[REDACTED]b"),
        ("ab", ("ab", "a"), "[REDACTED]"),
        ("abc", ("ab", "bc"), "[REDACTED]c"),
        ("aaa", ("a", "a"), "[REDACTED][REDACTED][REDACTED]"),
        ("aXa", ("a", "a"), "[REDACTED]X[REDACTED]"),
        ("secret", ("", "secret", ""), "[REDACTED]"),
        ("secret", ("",), "secret"),
        ("[REDACTED]", ("secret",), "[REDACTED]"),
    ],
)
def test_match_policy_order_overlap_and_duplicates(
    text: str,
    secrets: tuple[str, ...],
    expected: str,
) -> None:
    assert redact_value(text, secrets) == expected


def test_presence_is_checked_against_the_current_string() -> None:
    # The first replacement introduces text that the second secret matches.
    assert redact_value("x", ("x", "REDACTED")) == "[[REDACTED]]"


def test_nested_no_match_preserves_subclass_and_containers() -> None:
    value = Text("visible content")
    source: dict[str, Any] = {
        "text": value,
        "list": [value, 1],
        "tuple": (value, {"inner": value}),
        "unsupported": b"secret",
    }

    result = redact_value(source, ("absent",))

    assert result is not source
    assert list(result) == list(source)
    assert isinstance(result, dict)
    assert isinstance(result["list"], list)
    assert isinstance(result["tuple"], tuple)
    assert result["text"] is value
    assert result["list"][0] is value
    assert result["tuple"][0] is value
    assert result["tuple"][1]["inner"] is value
    assert result["unsupported"] is source["unsupported"]


def test_nested_match_redacts_and_preserves_container_types() -> None:
    source: dict[str, Any] = {
        "secret": "secret",
        "list": ["secret", (Text("secret"), 1)],
        "tuple": ("secret",),
    }

    result = redact_value(source, ("secret",))

    assert result == {
        "secret": "[REDACTED]",
        "list": ["[REDACTED]", ("[REDACTED]", 1)],
        "tuple": ("[REDACTED]",),
    }
    assert list(result) == list(source)
    assert isinstance(result, dict)
    assert isinstance(result["list"], list)
    assert isinstance(result["list"][1], tuple)
    assert isinstance(result["tuple"], tuple)
    assert type(result["list"][1][0]) is str
    assert not isinstance(result["list"][1][0], Text)


def test_nested_inputs_are_not_mutated() -> None:
    inner_list = ["secret"]
    inner_tuple = (Text("visible content"),)
    source: dict[str, Any] = {"list": inner_list, "tuple": inner_tuple}

    result = redact_value(source, ("secret",))

    assert source["list"] is inner_list
    assert source["tuple"] is inner_tuple
    assert inner_list == ["secret"]
    assert type(inner_tuple[0]) is Text
    assert result is not source
    assert result["list"] is not inner_list
    assert result["tuple"] is not inner_tuple
    assert result["list"] == ["[REDACTED]"]
    assert result["tuple"][0] is inner_tuple[0]


def test_dict_keys_are_never_processed_or_replaced() -> None:
    key = Text("secret")

    result = redact_value({key: "secret"}, ("secret",))

    assert list(result) == [key]
    assert list(result)[0] is key
    assert result[key] == "[REDACTED]"


def test_unsupported_values_pass_through_unchanged() -> None:
    marker = object()
    for value in (None, True, False, 0, 42, 3.5, marker, b"secret"):
        assert redact_value(value, ("secret",)) is value


def test_emitted_event_detail_keeps_subclass_on_no_match() -> None:
    inner = InMemoryEventSink()
    sink = RedactingEventSink(inner, ("absent",))
    value = Text("visible content")
    original_details = {"value": value, "nested": {"value": value}}
    event = Event(kind="TEST", details=original_details, producer_role="qa")

    sink.record(event)

    assert len(inner.events) == 1
    recorded = inner.events[0]
    assert recorded.kind == "TEST"
    assert recorded.producer_role == "qa"
    assert type(recorded.details["value"]) is Text
    assert recorded.details["value"] == "visible content"
    assert type(recorded.details["nested"]["value"]) is Text
    assert event.details is original_details
    assert original_details["value"] is value


def test_emitted_event_detail_is_plain_str_on_match() -> None:
    inner = InMemoryEventSink()
    sink = RedactingEventSink(inner, ("secret",))
    value = Text("visible secret")

    sink.record(Event(kind="TEST", details={"value": value}, producer_role="qa"))

    recorded = inner.events[0].details["value"]
    assert recorded == "visible [REDACTED]"
    assert type(recorded) is str
    assert not isinstance(recorded, Text)


def test_thin_adapter_matches_helper_for_subclass_values() -> None:
    value = Text("visible content")
    no_match = RedactingEventSink(InMemoryEventSink(), ("", "absent", ""))

    assert no_match._secrets == ("absent",)
    assert no_match._redact(value) is value
    assert no_match._redact({"k": value})["k"] is value

    matched_sink = RedactingEventSink(InMemoryEventSink(), ("secret",))
    matched = matched_sink._redact(Text("visible secret"))

    assert matched == "visible [REDACTED]"
    assert type(matched) is str
    assert matched == redact_value(Text("visible secret"), ("secret",))
    assert no_match._redact(value) == redact_value(value, no_match._secrets)
