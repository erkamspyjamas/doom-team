"""Doom Team worker: executes exactly ONE task attempt, then exits.

This is what runs on the far side of a Gas City job. It is deliberately the
smallest thing that can be trusted with an attempt: it reads a bounded
request, claims the work item through Gas City, runs the established
production task lifecycle once, writes one canonical result, and stops.

It does not plan, does not touch the DAG, does not allocate or spend recovery
budget, does not decide whether another attempt should happen, does not
advance the deliverable frontier, and does not approve anything. Every one of
those decisions stays with the mission layer, which is the only place that
can see the whole mission.

Credentials never travel in the request. The worker builds its inference
client from its own environment through the canonical configuration loader,
and it resolves its validation plan by NAME from trusted code - so a request
can describe which policy to apply but can never supply commands to run.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from doom_team.config import ConfigError, ConfigLoader
from doom_team.inference import build_inference_client, build_quality_inference_client
from doom_team.observability import DurableEventSink
from doom_team.subprocess_guard import run_guarded
from doom_team.task_runner import ProductionTaskRunner
from doom_team.transport import (
    TaskAttemptRequest,
    TransportError,
    resolve_validation_policy,
    validation_fingerprint,
    result_to_dict,
    transport_failure,
)

EXIT_OK = 0
EXIT_BAD_REQUEST = 1
EXIT_CLAIM_FAILED = 2
EXIT_WRITE_FAILED = 3


def claim_work(city: str, agent: str, bead_id: str, gc_binary: str) -> None:
    """Claim this attempt's work item through Gas City, fail closed.

    The claim is Gas City's, not ours: if it does not hand us this exact bead
    we refuse to do the work rather than execute unrouted or already-claimed
    work.
    """
    # The install directory, not the symlink target (see the transport).
    tools = [str(Path(gc_binary).parent), str(Path(gc_binary).resolve().parent)]
    env = dict(os.environ)
    env["PATH"] = ":".join([*tools, env.get("PATH", "")])
    completed = run_guarded(
        [gc_binary, "hook", "--city", city, "--claim", "--json", agent],
        cwd=city,
        check=False,
        text=True,
        capture_output=True,
        env=env,
    )
    if completed.returncode != 0:
        raise TransportError(
            f"gas city refused the work claim: {completed.stderr.strip()[:300]}"
        )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise TransportError(f"gas city claim response was not JSON: {exc}") from exc
    if not payload.get("ok") or payload.get("reason") != "claimed":
        raise TransportError(f"gas city did not claim work: {payload}")
    claimed = str(payload.get("bead_id", ""))
    if claimed != bead_id:
        raise TransportError(
            f"gas city claimed {claimed!r}, not this attempt's work item {bead_id!r}"
        )


def _secret_values() -> tuple[str, ...]:
    """Configured credential values, for scrubbing what this worker persists."""
    try:
        return ConfigLoader().load().redaction_secrets()
    except ConfigError:
        return ()


def run_attempt(request: TaskAttemptRequest) -> dict[str, Any]:
    """Execute one attempt through the established production lifecycle."""
    settings = ConfigLoader().load()
    sink = DurableEventSink(
        settings.state_dir, secret_values=settings.redaction_secrets()
    )
    client = build_inference_client(
        settings,
        event_sink=sink,
        producer_run=f"{request.mission_id}/{request.task_id}",
    )
    commands = resolve_validation_policy(
        request.validation_policy, typecheck_target=settings.python_typecheck_target,
    )
    if {request.validation_policy, settings.validation_policy} & {"python-quality", "project"}:
        if (
            request.validation_policy != settings.validation_policy
            or request.validation_fingerprint != validation_fingerprint(request.validation_policy, commands)
        ):
            raise TransportError("worker validation policy/target differs from the pinned request")
    runner = ProductionTaskRunner(
        client, validation_commands=commands,
        validation_timeout=settings.validation_command_timeout,
        quality_inference=build_quality_inference_client(
            settings, event_sink=sink,
            producer_run=f"{request.mission_id}/{request.task_id}/quality",
        ) if settings.quality_model_route == "fallback" else None,
    )
    result = runner.run_task(
        request.task,
        request.decision,
        request.specialist(),
        request.workspace(),
        objective_summary=request.objective_summary,
        dependency_summary=request.dependency_summary,
        remediation_summary=request.remediation_summary,
    )
    return result_to_dict(result)


def main(argv: list[str] | None = None) -> int:
    """Read a request, execute one attempt, write one result, exit."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        print("usage: doom-team-worker <request.json> <result.json>", file=sys.stderr)
        return EXIT_BAD_REQUEST

    request_path, result_path = Path(args[0]), Path(args[1])
    try:
        request = TaskAttemptRequest.from_dict(json.loads(request_path.read_text()))
    except (OSError, json.JSONDecodeError, TransportError) as exc:
        print(f"worker: unusable request: {exc}", file=sys.stderr)
        return EXIT_BAD_REQUEST

    envelope = json.loads(request_path.read_text())
    city = str(envelope.get("gas_city", {}).get("city", ""))
    agent = str(envelope.get("gas_city", {}).get("agent", ""))
    bead_id = str(envelope.get("gas_city", {}).get("bead_id", ""))
    gc_binary = str(envelope.get("gas_city", {}).get("gc_binary", ""))

    try:
        if city:
            claim_work(city, agent, bead_id, gc_binary)
    except TransportError as exc:
        print(f"worker: {exc}", file=sys.stderr)
        return EXIT_CLAIM_FAILED

    try:
        payload = run_attempt(request)
    except (ConfigError, TransportError) as exc:
        # A worker that cannot even start is a failed attempt, reported in the
        # canonical vocabulary - never a silent success.
        payload = result_to_dict(transport_failure(request, f"worker setup failed: {exc}"))

    body = {
        "schema_version": 1,
        "mission_id": request.mission_id,
        "task_id": request.task_id,
        "attempt": request.attempt,
        "baseline_sha": request.baseline_sha,
        # Echoed so the transport can tell this result from any other file
        # that happens to appear where it expects one.
        "nonce": request.nonce,
        "result": payload,
    }
    try:
        result_path.parent.mkdir(parents=True, exist_ok=True)
        # The job directory sits outside the mission ledger's redaction
        # boundary, so validation output is scrubbed before it is written.
        text = json.dumps(body, sort_keys=True, indent=2)
        for secret in _secret_values():
            text = text.replace(secret, "[REDACTED]")
        result_path.write_text(text)
    except OSError as exc:
        print(f"worker: cannot write result: {exc}", file=sys.stderr)
        return EXIT_WRITE_FAILED
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
