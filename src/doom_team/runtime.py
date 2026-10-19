"""Standalone runtime assembly: where a mission's task attempts execute.

One place decides the execution transport, so the rest of the runtime never
asks. :func:`build_task_execution_transport` reads the operator's configured
choice and returns either the in-process path that Gate A proved or a real Gas
City transport; nothing infers a transport from ambient state, and a
configured Gas City is never quietly downgraded to running work in-process.

The Gas City runtime this returns is Doom Team's own: its city lives where the
operator configured it, separate from any Autopilot city, and outside the
deliverable repository. If that city does not exist yet it is initialized
once, non-interactively, so a single product command can run a whole mission
without the operator hand-driving Gas City.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from doom_team.config import TRANSPORT_GAS_CITY, TRANSPORT_IN_PROCESS, ConfigError, Settings
from doom_team.gas_city_transport import GasCityTaskTransport
from doom_team.inference import build_quality_inference_client
from doom_team.observability import EventSink
from doom_team.ports import InferencePort
from doom_team.task_runner import ProductionTaskRunner
from doom_team.transport import (
    InProcessTaskTransport,
    TaskExecutionTransport,
    resolve_validation_policy,
)

CITY_INIT_TIMEOUT = 900.0


class RuntimeError_(Exception):
    """Raised when the standalone runtime cannot be assembled (fail closed)."""


def _gc_env(gc_binary: str) -> dict[str, str]:
    """`gc` shells out to its sibling tools, so they must be on PATH.

    The install directory, not the symlink target: `gc` is commonly a symlink
    into a versioned release directory that contains only `gc`.
    """
    import os

    tools = [str(Path(gc_binary).parent), str(Path(gc_binary).resolve().parent)]
    env = dict(os.environ)
    env["PATH"] = ":".join([*tools, env.get("PATH", "")])
    return env


def ensure_gas_city(city: str | Path, gc_binary: str | Path) -> Path:
    """Return a usable Doom Team city, initializing it once if absent.

    Non-interactive and idempotent: an existing city at the configured path is
    used as-is.

    Doom Team never registers the city with the machine-wide supervisor and
    passes ``--no-start``, but Gas City still brings up its own local bead
    store daemon for the city and keeps it running after this process exits.
    That daemon belongs to Gas City's runtime, not to Doom Team, and Doom Team
    does not stop it.
    """
    path = Path(city)
    try:
        if (path / "city.toml").exists():
            return path
        if path.exists() and any(path.iterdir()):
            raise RuntimeError_(
                f"gas city path {path} exists, is not a city, and is not empty"
            )
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RuntimeError_(f"gas city path {path} is unusable: {exc!r}") from exc
    try:
        completed = subprocess.run(  # noqa: S603 - argv-only, operator-configured
            [
                str(gc_binary), "init", "--template", "minimal",
                "--default-provider", "claude", "--skip-provider-readiness",
                "--no-start", "--yes", "--json", "--name", path.name, str(path),
            ],
            cwd=str(path.parent), capture_output=True, text=True,
            timeout=CITY_INIT_TIMEOUT, env=_gc_env(str(gc_binary)),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError_(f"could not initialize the gas city at {path}: {exc!r}") from exc
    if not (path / "city.toml").exists():
        detail = (completed.stderr or completed.stdout or "").strip()[:400]
        raise RuntimeError_(f"gas city initialization failed at {path}: {detail}")
    return path


def build_task_execution_transport(
    settings: Settings,
    inference: InferencePort,
    *,
    validation_policy: str | None = None,
    event_sink: EventSink | None = None,
) -> TaskExecutionTransport:
    """Build the configured transport. Never silently substitutes another.

    A misconfigured or unavailable Gas City is an error here, not a quiet
    fallback to running the work in this process: an operator who asked for
    out-of-process execution must not get in-process execution without being
    told.
    """
    policy = settings.validation_policy if validation_policy is None else validation_policy
    commands = resolve_validation_policy(policy, typecheck_target=settings.python_typecheck_target)
    if settings.task_transport == TRANSPORT_IN_PROCESS:
        return InProcessTaskTransport(
            ProductionTaskRunner(
                inference, validation_commands=commands,
                validation_timeout=settings.validation_command_timeout,
                quality_inference=build_quality_inference_client(
                    settings, event_sink=event_sink, producer_run="quality"
                ) if settings.quality_model_route == "fallback" else None,
            )
        )
    if settings.task_transport != TRANSPORT_GAS_CITY:
        raise RuntimeError_(f"unknown task transport: {settings.task_transport!r}")

    if not settings.gas_city_binary or not settings.gas_city_city:
        raise ConfigError("gas city transport requires gas_city_binary and gas_city_city")
    city = ensure_gas_city(settings.gas_city_city, settings.gas_city_binary)
    return GasCityTaskTransport(
        city=city,
        gc_binary=settings.gas_city_binary,
        # Job artifacts are runtime state, so they live beside the mission
        # ledger - never inside the deliverable repository.
        jobs_root=settings.state_dir / "gas-city-jobs",
        agent=settings.gas_city_agent,
        timeout=settings.gas_city_attempt_timeout,
        event_sink=event_sink,
        secret_values=settings.redaction_secrets(),
    )
