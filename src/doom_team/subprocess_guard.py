"""Practical subprocess safety helpers.

Provides environment sanitization (strips credential/sensitive variables),
argv-only execution (no shell), and best-effort network isolation via
`unshare --net` on Linux where available. These are mitigations, not a
security sandbox; residual risks are documented in THREAT_MODEL.md.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from typing import Any

SENSITIVE_SUBSTRINGS: tuple[str, ...] = (
    "API_KEY",
    "SECRET",
    "TOKEN",
    "PASSWORD",
    "PASSWD",
    "CREDENTIAL",
    "PRIVATE_KEY",
    "AWS_ACCESS_KEY",
    "AWS_SECRET_KEY",
    "AUTHORIZATION",
    "DOOM_API_KEY",
)


def is_sensitive_env_key(name: str) -> bool:
    """Return True if the environment variable name looks sensitive."""
    upper = name.upper()
    return any(part in upper for part in SENSITIVE_SUBSTRINGS)


def sanitize_env(
    env: Mapping[str, str] | None = None,
    extra_denylist: Sequence[str] = (),
) -> dict[str, str]:
    """Return a copy of the environment without credential/sensitive variables."""
    source = dict(os.environ) if env is None else dict(env)
    denylist = SENSITIVE_SUBSTRINGS + tuple(extra_denylist)
    return {
        key: value
        for key, value in source.items()
        if not any(part in key.upper() for part in denylist)
    }


_network_isolation_checked = False
_network_isolation_available = False


def network_isolation_available() -> bool:
    """Return True if `unshare --net` works on this platform/process."""
    global _network_isolation_checked, _network_isolation_available
    if _network_isolation_checked:
        return _network_isolation_available
    _network_isolation_checked = True
    if sys.platform != "linux":
        _network_isolation_available = False
        return False
    unshare = shutil.which("unshare")
    if unshare is None:
        _network_isolation_available = False
        return False
    try:
        result = subprocess.run([unshare, "--net", "true"], capture_output=True, timeout=5)
        _network_isolation_available = result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        _network_isolation_available = False
    return _network_isolation_available


def network_isolation_prefix() -> list[str]:
    """Return the command prefix for network isolation, or an empty list."""
    if network_isolation_available():
        return ["unshare", "--net"]
    return []


def run_guarded(
    args: Sequence[str],
    *,
    cwd: str | os.PathLike[str] | None = None,
    input: bytes | str | None = None,
    env: Mapping[str, str] | None = None,
    extra_denylist: Sequence[str] = (),
    check: bool = True,
    text: bool = True,
    capture_output: bool = True,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[Any]:
    """Run a subprocess with argv-only execution, sanitized env, and network isolation."""
    cmd = list(args)
    prefix = network_isolation_prefix()
    if prefix:
        cmd = prefix + cmd
    sanitized = sanitize_env(env, extra_denylist)
    return subprocess.run(
        cmd,
        cwd=cwd,
        input=input,
        env=sanitized,
        check=check,
        text=text,
        capture_output=capture_output,
        timeout=timeout,
    )
