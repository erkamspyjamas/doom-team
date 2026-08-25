"""Durable-state helpers: atomic writes, checksums, and corruption errors.

All durable stores use these helpers to guarantee that state is written
atomically (write-unique-temp-fsync-rename) and that reads fail closed when a
record is missing, malformed, or fails checksum verification.

Atomic write semantics: each write uses a unique temporary file in the same
directory as the destination. Concurrent writers therefore last-write-wins
atomically: every writer fsyncs its own unique temp and atomically renames it
over the destination with ``os.replace``, so there is never torn state. A crash
between temp creation and rename leaves a unique stale temp (named
``<destination>.<random>.tmp``) that never blocks future writes (each
subsequent write uses a fresh unique name), is never read as destination state,
and is never deleted by the service (we never remove foreign files).
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any


class StateCorruptionError(Exception):
    """Raised when durable state is missing, malformed, or fails checksum."""


def checksum_hex(data: bytes) -> str:
    """Return the sha256 hex digest of the given bytes."""
    return hashlib.sha256(data).hexdigest()


def _write_all(fd: int, data: bytes) -> None:
    """Write all of ``data`` to ``fd``, handling partial writes."""
    view = memoryview(data)
    total = 0
    while total < len(view):
        total += os.write(fd, view[total:])


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write bytes atomically: unique temp, fsync, atomic rename, fsync dir."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
    )
    tmp = Path(tmp_name)
    try:
        _write_all(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(str(tmp), str(path))
    dir_fd = os.open(str(path.parent), os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def atomic_write_text(path: Path, text: str) -> None:
    """Write text atomically."""
    atomic_write_bytes(path, text.encode("utf-8"))


def atomic_write_json(
    path: Path, payload: dict[str, Any], *, checksum_key: str = "checksum"
) -> None:
    """Write a JSON object with an embedded checksum over the payload.

    The checksum is computed over the canonical JSON body (without the
    checksum key) so that reads can detect any tampering or corruption.
    """
    body = {k: v for k, v in payload.items() if k != checksum_key}
    canonical = json.dumps(
        body, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    digest = "sha256:" + checksum_hex(canonical)
    full = json.dumps(
        {**body, checksum_key: digest}, sort_keys=True, indent=2, default=str
    )
    atomic_write_text(path, full)


def read_json_checked(path: Path, *, checksum_key: str = "checksum") -> dict[str, Any]:
    """Read a JSON object, verifying its checksum; raise on any issue."""
    try:
        text = path.read_text()
    except OSError as exc:
        raise StateCorruptionError(f"cannot read state file {path}: {exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise StateCorruptionError(f"corrupt JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise StateCorruptionError(f"state file {path} is not a JSON object")
    if checksum_key not in data:
        raise StateCorruptionError(f"state file {path} missing {checksum_key}")
    stored = data.pop(checksum_key)
    if not isinstance(stored, str) or not stored.startswith("sha256:"):
        raise StateCorruptionError(f"state file {path} has invalid {checksum_key}")
    canonical = json.dumps(
        data, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    if "sha256:" + checksum_hex(canonical) != stored:
        raise StateCorruptionError(f"state file {path} failed checksum verification")
    return data
