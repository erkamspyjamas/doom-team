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

    Strings replace each nonempty secret that actually occurs in the string
    being processed, in the supplied order, with ``[REDACTED]``. Because
    replacement is only invoked on an actual occurrence, a string containing
    none of the secrets is returned as the very same object -- a ``str``
    subclass therefore keeps its type and identity -- while a string that does
    match yields the plain ``str`` produced by ``str.replace``. Dict keys are
    left untouched while values are processed recursively. Lists and tuples are
    rebuilt as the same built-in container type, preserving ordering. Any other
    value passes through unchanged.
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
    """Replace each present, nonempty secret of ``text`` in supplied order.

    ``str.replace`` is only invoked when the secret occurs in the string being
    processed at that point, matching the original ``RedactingEventSink``
    behavior: a no-match string (including a ``str`` subclass) is returned as
    the same object, and a match returns the base ``str`` from ``str.replace``.
    """
    redacted = text
    for secret in secret_values:
        if not secret or secret not in redacted:
            continue
        redacted = redacted.replace(secret, REDACTED)
    return redacted
