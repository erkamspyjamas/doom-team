"""Real Gas City task execution transport.

One Doom Team task attempt becomes one Gas City work item. Gas City creates
and routes the work item, and the Doom Team worker must claim it through Gas
City before it is allowed to do anything - so the work really crosses the Gas
City boundary rather than being a private queue wearing its name.

The division of labour is deliberate. Gas City schedules and hands out work.
Doom Team decides what the work is, which specialist does it, which frontier
it is pinned to, whether the returned result is trustworthy, whether another
attempt is allowed, and whether the mission may advance. Nothing about the
mission lifecycle lives here.

This transport performs exactly ONE bounded execution per call. It never
retries: a Gas City outage, a worker that never starts, a missing or
malformed result, a wrong identity or a timeout all become one failed
attempt in the canonical vocabulary, and the mission layer - which owns the
recovery budget - decides what happens next.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import fcntl
import secrets
import subprocess
import sys
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from doom_team.observability import Event, EventSink
from doom_team.subprocess_guard import run_guarded
from doom_team.task_runner import TaskExecutionResult
from doom_team.transport import (
    TaskAttemptRequest,
    TransportError,
    result_from_dict,
    transport_failure,
    verify_result_identity,
)

DEFAULT_ATTEMPT_TIMEOUT = 900.0
# A claim cannot be addressed at a specific work item, so the agent's queue
# must hold only ours when the worker claims. Draining is bounded: an endless
# supply of foreign items is a misconfigured city, not something to spin on.
MAX_STALE_RELEASES = 16
MAX_DIAGNOSTIC_CHARS = 2_000

# Job directories are built from these, so they must be plain identifiers.
_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$")


def _safe(value: str, field: str) -> str:
    if not _SAFE_COMPONENT.fullmatch(value) or ".." in value:
        raise TransportError(f"unsafe {field} for a job path: {value!r}")
    return value


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


@dataclass(frozen=True)
class TransportProvenance:
    """Bounded diagnostic record of one transport execution.

    Provenance, never acceptance evidence: whether a task is accepted is
    decided solely by the canonical result's own validation, QA and Reviewer
    outcomes.
    """

    job_id: str
    agent: str
    submitted_at: str
    completed_at: str
    terminal_state: str
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "agent": self.agent,
            "submitted_at": self.submitted_at,
            "completed_at": self.completed_at,
            "terminal_state": self.terminal_state,
            "detail": self.detail[:MAX_DIAGNOSTIC_CHARS],
        }


class GasCityTaskTransport:
    """Executes one task attempt as one real Gas City work item."""

    def __init__(
        self,
        *,
        city: str | Path,
        gc_binary: str | Path,
        jobs_root: str | Path,
        agent: str = "mayor",
        worker_argv: tuple[str, ...] = (sys.executable, "-m", "doom_team.worker"),
        timeout: float = DEFAULT_ATTEMPT_TIMEOUT,
        event_sink: EventSink | None = None,
        worker_env: Mapping[str, str] | None = None,
        secret_values: tuple[str, ...] = (),
    ) -> None:
        self._city = Path(city)
        self._gc = str(gc_binary)
        self._jobs_root = Path(jobs_root)
        self._agent = agent
        self._worker_argv = tuple(worker_argv)
        self._timeout = timeout
        self._event_sink = event_sink
        # The worker is Doom Team's OWN code and legitimately needs provider
        # access, so it inherits the runtime environment rather than the
        # credential-stripped one used for untrusted validation commands.
        # Credentials therefore reach it only the sanctioned way - through the
        # environment of the process that started the transport - and never
        # through the request envelope, the work item, argv, or any log.
        self._worker_env = dict(worker_env) if worker_env is not None else dict(os.environ)
        # `gc` shells out to its sibling tools (bd, dolt), so it needs its own
        # install directory on PATH. Without it the work-item calls fail
        # silently and an assignment is never released.
        # The INSTALL directory, not the symlink target: `gc` is commonly a
        # symlink into a versioned release dir that holds only `gc`, while its
        # sibling tools live beside the link.
        tools = [str(Path(self._gc).parent), str(Path(self._gc).resolve().parent)]
        self._gc_env = dict(os.environ)
        self._gc_env["PATH"] = ":".join([*tools, self._gc_env.get("PATH", "")])
        self._secret_values = tuple(v for v in secret_values if v)
        self.last_provenance: TransportProvenance | None = None

    def _scrub(self, text: str) -> str:
        """Job artifacts live outside the ledger, so they are scrubbed here."""
        for secret in self._secret_values:
            text = text.replace(secret, "[REDACTED]")
        return text

    # -- Gas City ---------------------------------------------------------

    def _gc_json(self, args: list[str], what: str) -> dict[str, Any]:
        try:
            completed = run_guarded(
                [self._gc, *args], cwd=str(self._city), check=False, text=True,
                capture_output=True, timeout=self._timeout, env=self._gc_env,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise TransportError(f"gas city {what} failed: {exc!r}") from exc
        if completed.returncode != 0:
            raise TransportError(
                f"gas city {what} failed: {str(completed.stderr).strip()[:300]}"
            )
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise TransportError(f"gas city {what} did not return JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise TransportError(f"gas city {what} returned a non-object payload")
        return payload

    def _submit(self, request: TaskAttemptRequest) -> str:
        """Create and route one Gas City work item for this attempt."""
        title = (
            f"doom-team attempt {request.attempt} "
            f"{request.mission_id}/{request.task_id}"
        )
        # --no-convoy keeps the mapping exact: one attempt is one work item.
        # An auto-created convoy bead would stay routed to the agent and make
        # the NEXT attempt's claim return a stale assignment.
        payload = self._gc_json(
            ["sling", "--city", str(self._city), "--json", "--no-convoy", self._agent, title],
            "sling",
        )
        if not payload.get("ok") or not payload.get("routed"):
            raise TransportError(f"gas city did not route the work item: {payload}")
        job_id = str(payload.get("bead_id", ""))
        if not job_id:
            raise TransportError("gas city returned no work item id")
        return job_id

    def _close(self, job_id: str, outcome: str) -> None:
        """Complete the work item in Gas City's own vocabulary.

        Releasing the assignment matters: an agent holds one work item at a
        time, so leaving it open would make the NEXT attempt's claim return a
        stale assignment. The outcome is provenance - it records what became
        of the attempt and never decides whether the task was accepted.
        """
        for args in (
            ["bd", "--city", str(self._city), "update", job_id,
             "--set-metadata", f"gc.work_outcome={outcome}"],
            ["bd", "--city", str(self._city), "close", job_id],
        ):
            try:
                run_guarded(
                    [self._gc, *args], cwd=str(self._city), check=False, text=True,
                    capture_output=True, timeout=120.0, env=self._gc_env,
                )
            except (OSError, subprocess.TimeoutExpired):
                return

    # -- execution --------------------------------------------------------

    def _job_dir(self, request: TaskAttemptRequest) -> Path:
        return (
            self._jobs_root
            / _safe(request.mission_id, "mission id")
            / _safe(request.task_id, "task id")
            / f"attempt-{int(request.attempt)}"
        )

    def _emit(self, kind: str, details: dict[str, Any]) -> None:
        if self._event_sink is not None:
            self._event_sink.record(
                Event(kind=kind, details=details, producer_role="gas-city-transport")
            )

    def execute(self, request: TaskAttemptRequest) -> TaskExecutionResult:
        """Run exactly one attempt through Gas City. Never retries."""
        submitted = _now()
        job_id = ""
        try:
            job_dir = self._job_dir(request)
            job_dir.mkdir(parents=True, exist_ok=True)
            result_path = job_dir / "result.json"
            # A per-attempt secret binds the result to this request.
            request = replace(request, nonce=secrets.token_hex(16))

            with self._city_turn():
                # Anything already routed to the agent would be handed to our
                # worker instead of our own item, so the queue is cleared
                # first and held for the whole submit-claim-complete cycle.
                self._release_stale()
                job_id = self._submit(request)
                envelope = request.to_dict()
                envelope["gas_city"] = {
                    "city": str(self._city),
                    "agent": self._agent,
                    "bead_id": job_id,
                    "gc_binary": self._gc,
                }
                request_path = job_dir / "request.json"
                request_path.write_text(json.dumps(envelope, sort_keys=True, indent=2))
                self._emit(
                    "TRANSPORT_SUBMITTED",
                    {
                        "mission_id": request.mission_id,
                        "task_id": request.task_id,
                        "attempt": request.attempt,
                        "job_id": job_id,
                    },
                )
                detail = self._run_worker(request_path, result_path)

            if detail:
                self._finish(request, "worker_failed", submitted, detail, job_id)
                return transport_failure(request, self._scrub(detail))

            result = self._consume(request, result_path)
            self._finish(request, "completed", submitted, "", job_id)
            return result
        except TransportError as exc:
            self._finish(request, "transport_failed", submitted, str(exc), job_id)
            return transport_failure(request, self._scrub(str(exc)))
        except Exception as exc:  # boundary: always one typed failed attempt
            self._finish(request, "transport_failed", submitted, repr(exc), job_id)
            return transport_failure(request, self._scrub(repr(exc)))
        finally:
            # The work item is always completed, on every path, so the agent's
            # queue never carries a stale assignment into the next attempt.
            if job_id:
                self._close(job_id, "shipped")

    def _release_stale(self) -> None:
        """Clear the agent's queue of work items that are not this attempt's.

        Anything already routed to the agent - left by a crash, or by another
        run sharing this city - would otherwise be handed to our worker
        instead of our own item, costing a real attempt from the mission's
        bounded budget for work it never did.
        """
        for _ in range(MAX_STALE_RELEASES):
            try:
                completed = run_guarded(
                    [self._gc, "hook", "--city", str(self._city), "--claim", "--json",
                     self._agent],
                    cwd=str(self._city), check=False, text=True, capture_output=True,
                    timeout=120.0, env=self._gc_env,
                )
                payload = json.loads(completed.stdout) if completed.stdout.strip() else {}
            except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
                return
            stale = str(payload.get("bead_id", ""))
            if not stale or payload.get("reason") not in ("existing_assignment", "claimed"):
                return
            self._close(stale, "abandoned")

    @contextmanager
    def _city_turn(self) -> Iterator[None]:
        """Hold the city's single work queue for one attempt.

        Gas City routes to one agent slot, so two missions sharing a city
        would otherwise claim each other's items. A kernel lock keeps
        submit-claim-complete atomic per city, and it is released
        automatically if the holder dies.
        """
        self._city.mkdir(parents=True, exist_ok=True)
        handle = os.open(str(self._city / ".doom-team-queue.lock"),
                         os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(handle, fcntl.LOCK_EX)
            yield
        finally:
            os.close(handle)

    def _run_worker(self, request_path: Path, result_path: Path) -> str:
        """Launch the worker once. Returns a failure detail, or "" on success."""
        try:
            # argv-only, no shell, hard timeout, exactly one launch.
            completed = subprocess.run(  # noqa: S603
                [*self._worker_argv, str(request_path), str(result_path)],
                cwd=str(request_path.parent), check=False, text=True,
                capture_output=True, timeout=self._timeout, env=self._worker_env,
            )
        except subprocess.TimeoutExpired:
            return f"worker exceeded the {self._timeout:g}s attempt timeout"
        except OSError as exc:
            return f"worker could not be launched: {exc!r}"
        if completed.returncode != 0:
            return (
                f"worker exited {completed.returncode}: "
                f"{str(completed.stderr).strip()[:MAX_DIAGNOSTIC_CHARS]}"
            )
        if not result_path.exists():
            return "worker exited without producing a result"
        return ""

    def _consume(self, request: TaskAttemptRequest, result_path: Path) -> TaskExecutionResult:
        """Read the result envelope and refuse anything that is not this attempt's."""
        try:
            body = json.loads(result_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise TransportError(f"unreadable result envelope: {exc}") from exc
        if not isinstance(body, dict) or body.get("schema_version") != 1:
            raise TransportError("result envelope has an unsupported schema")
        try:
            attempt = int(body.get("attempt"))  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise TransportError(f"result envelope has an unusable attempt: {exc}") from exc
        identity = (str(body.get("mission_id", "")), str(body.get("task_id", "")), attempt)
        if identity != request.identity:
            raise TransportError(
                f"result envelope is for {identity}, not this attempt {request.identity}"
            )
        if str(body.get("baseline_sha", "")) != request.baseline_sha:
            raise TransportError("result envelope is pinned to a different baseline")
        if str(body.get("nonce", "")) != request.nonce:
            raise TransportError("result envelope does not answer this request")
        payload = body.get("result")
        if not isinstance(payload, dict):
            raise TransportError("result envelope carries no result object")
        result = result_from_dict(payload)
        verify_result_identity(request, result)
        return result

    def _finish(
        self,
        request: TaskAttemptRequest,
        state: str,
        submitted: str,
        detail: str,
        job_id: str,
    ) -> None:
        self.last_provenance = TransportProvenance(
            job_id=job_id,
            agent=self._agent,
            submitted_at=submitted,
            completed_at=_now(),
            terminal_state=state,
            detail=detail,
        )
        try:
            job_dir = self._job_dir(request)
            job_dir.mkdir(parents=True, exist_ok=True)
            payload = self.last_provenance.as_dict()
            payload["detail"] = self._scrub(str(payload.get("detail", "")))
            (job_dir / "transport.json").write_text(
                json.dumps(payload, sort_keys=True, indent=2)
            )
        except (OSError, TransportError):
            pass
        self._emit(
            "TRANSPORT_FINISHED",
            {
                "mission_id": request.mission_id,
                "task_id": request.task_id,
                "attempt": request.attempt,
                "job_id": job_id,
                "terminal_state": state,
            },
        )
