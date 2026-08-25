"""Durable-state helpers: atomic writes, checksums, and corruption errors.

All durable stores use these helpers to guarantee that state is written
atomically (write-temp-fsync-rename) and that reads fail closed when a
record is missing, malformed, or fails checksum verification.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


class StateCorruptionError(Exception):
    """Raised when durable state is missing, malformed, or fails checksum."""


def checksum_hex(data: bytes) -> str:
    """Return the sha256 hex digest of the given bytes."""
    return hashlib.sha256(data).hexdigest()


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write bytes atomically: temp file, fsync, rename, fsync dir."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, data)
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
