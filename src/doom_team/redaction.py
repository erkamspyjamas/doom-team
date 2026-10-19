"""Pure event-value redaction helper.

This module is deliberately free of I/O: it reads no environment variables,
configuration or files, performs no network access and writes no logs. It only
rewrites the value it is given, returning a new object tree, and never mutates
its input.
"""

from __future__ import annotations

from typing import Any

REDACTED = "[REDACTED]"


def redact_value(value: Any, secret_values: tuple[str, ...]) -> Any:
    """Return a copy of ``value`` with configured secrets replaced.

    Strings replace each nonempty secret in the supplied order with
    ``[REDACTED]``. Dict keys are left untouched while values are processed
    recursively. Lists and tuples are rebuilt as the same built-in container
    type, preserving ordering. Any other value passes through unchanged.
    """
    if isinstance(value, str):
        return _redact_string(value, secret_values)
    if isinstance(value, dict):
        return {
            key: redact_value(item, secret_values) for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_value(item, secret_values) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_value(item, secret_values) for item in value)
    return value


def _redact_string(text: str, secret_values: tuple[str, ...]) -> str:
    redacted = text
    for secret in secret_values:
        if not secret:
            continue
        redacted = redacted.replace(secret, REDACTED)
    return redacted
