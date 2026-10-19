"""Tests for the pure event-value redaction helper."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import Any

import doom_team.redaction as redaction_module
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
