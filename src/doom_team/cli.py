"""Command-line interface for the Doom Team service.

Provides subcommands: run, serve, status, doctor, approve, backup, restore.

Exit codes:
    0 - success / healthy
    1 - configuration error
    2 - state or store error
    3 - approval failure
    4 - run failure (planning, routing, or a mission that did not reach the
        human approval gate)
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from doom_team.approval import DurableApprovalPort
from doom_team.backup import backup_state, restore_state
from doom_team.config import ConfigError, ConfigLoader, Settings
from doom_team.durable import StateCorruptionError
from doom_team.evidence import DurableEvidenceStore
from doom_team.inference import build_inference_client
from doom_team.mission import (
    MISSION_ACTION_PREFIX,
    MISSION_APPROVED,
    MISSION_AWAITING_HUMAN_APPROVAL,
    MissionLedgerError,
    MissionStore,
    approve_mission,
)
from doom_team.mission_runner import MissionRunError, MissionRunner
from doom_team.planner import Planner, PlanningError
from doom_team.routing import RoutingError, route_task
from doom_team.observability import (
    DurableEventSink,
    Event,
    EventHistory,
    check_health,
    check_readiness,
)
from doom_team.verdicts import DurableVerdictStore

EXIT_SUCCESS = 0
EXIT_CONFIG_ERROR = 1
EXIT_STATE_ERROR = 2
EXIT_APPROVAL_ERROR = 3
EXIT_RUN_ERROR = 4


def _add_state_dir(parser: argparse.ArgumentParser) -> None:
    """Add ``--state-dir`` that never overwrites an explicit CLI value.

    ``default=SUPPRESS`` means the attribute is only set on the namespace when
    the user actually passes the flag, so a subparser default can never clobber
    an explicit value supplied in the parent position (or vice versa). The
    effective state directory is resolved once in :func:`_resolve_state_dir`.
    """
    parser.add_argument(
        "--state-dir",
        default=argparse.SUPPRESS,
        help="Override the durable state directory (always wins over DOOM_STATE_DIR)",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="doom-team",
        description="Doom Team service entrypoint",
    )
    _add_state_dir(parser)
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser(
        "run", help="Run a natural-language software objective through Doom Team"
    )
    _add_state_dir(run_parser)
    run_parser.add_argument("objective", help="Natural-language software objective")
    run_parser.add_argument(
        "--plan-only",
        action="store_true",
        help="Plan and route the objective without executing any work",
    )
    run_parser.add_argument(
        "--destination",
        default=None,
        help="Deliverable repository path (required unless --plan-only)",
    )

    resume_parser = subparsers.add_parser(
        "resume", help="Resume an interrupted mission from durable state"
    )
    _add_state_dir(resume_parser)
    resume_parser.add_argument("mission_id", help="Durable mission identity")

    serve_parser = subparsers.add_parser(
        "serve", help="Run the service loop with graceful shutdown"
    )
    _add_state_dir(serve_parser)

    status_parser = subparsers.add_parser(
        "status", help="Show operator-readable current state"
    )
    _add_state_dir(status_parser)

    doctor_parser = subparsers.add_parser(
        "doctor", help="Validate configuration and durable state"
    )
    _add_state_dir(doctor_parser)

    approve_parser = subparsers.add_parser(
        "approve", help="Record a human approval (never deploys)"
    )
    _add_state_dir(approve_parser)
    approve_parser.add_argument(
        "--mission-id",
        default=None,
        help="Approve a completed mission by its durable mission identity",
    )
    approve_parser.add_argument(
        "--action", default=None, help="Approval action (implied by --mission-id)"
    )
    approve_parser.add_argument(
        "--frontier-sha", default=None, help="Frontier SHA (implied by --mission-id)"
    )
    approve_parser.add_argument("--operator", required=True, help="Operator identity")
    approve_parser.add_argument(
        "--decision",
        required=True,
        choices=["approve", "deny", "true", "false"],
        help="Approval decision (approve|deny|true|false)",
    )
    approve_parser.add_argument("--contract-revision", default=None)
    approve_parser.add_argument("--roadmap-revision", default=None)

    backup_parser = subparsers.add_parser(
        "backup", help="Create a verified snapshot of durable state"
    )
    _add_state_dir(backup_parser)
    backup_parser.add_argument("--backup-dir", required=True, help="Backup destination root")

    restore_parser = subparsers.add_parser(
        "restore", help="Restore durable state from a verified backup"
    )
    _add_state_dir(restore_parser)
    restore_parser.add_argument("--backup-path", required=True, help="Path to a backup")

    return parser


def _resolve_state_dir(args: argparse.Namespace) -> str | None:
    """Return the effective state directory: explicit CLI > ``DOOM_STATE_DIR``.

    The CLI flag may be supplied in either the parent position
    (``doom-team --state-dir /x backup ...``) or the subcommand position
    (``doom-team backup --state-dir /x ...``); both resolve to the same single
    value here. The environment variable is used only when no CLI value is
    given. There is no default for ``state_dir``; it is required.
    """
    cli_value = getattr(args, "state_dir", None)
    if cli_value:
        return str(cli_value)
    return os.environ.get("DOOM_STATE_DIR")


def _load_settings(args: argparse.Namespace) -> Settings:
    """Load settings with the single resolved state directory."""
    return ConfigLoader().load(state_dir=_resolve_state_dir(args))


def _make_event_sink(settings: Settings) -> DurableEventSink:
    return DurableEventSink(settings.state_dir, secret_values=settings.redaction_secrets())


def _cmd_run(
    settings: Settings, objective: str, *, plan_only: bool, destination: str | None
) -> int:
    """Plan an objective, and unless plan-only, execute the whole mission.

    Plan-only is deliberately ephemeral: it creates no project repository, no
    workspace, and no durable mission state, and it never executes work.

    Execution runs the canonical :class:`MissionRunner`: plan, automatic
    routing, serial task execution with real validation, QA and Reviewer,
    frontier advancement, and a fresh final validation, ending at the human
    approval gate. It never pushes, deploys, or configures a remote.
    """
    if not plan_only:
        return _cmd_run_mission(settings, objective, destination)
    try:
        client = build_inference_client(settings)
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    try:
        mission = Planner(client).plan(objective)
        decisions = [route_task(task) for task in mission.tasks]
    except (PlanningError, RoutingError) as exc:
        print(f"planning error: {exc}", file=sys.stderr)
        return EXIT_RUN_ERROR
    output = {
        "mission_id": mission.id,
        "objective": {
            "id": mission.objective.id,
            "description": mission.objective.description,
        },
        "tasks": [
            {
                "task_id": task.id,
                "instruction": task.instruction,
                "dependencies": list(task.dependencies),
                "required_capabilities": list(task.required_capabilities),
                "acceptance_criteria": list(task.acceptance_criteria),
            }
            for task in mission.tasks
        ],
        "routing": [decision.as_dict() for decision in decisions],
    }
    print(json.dumps(output, sort_keys=True))
    return EXIT_SUCCESS


def _cmd_run_mission(settings: Settings, objective: str, destination: str | None) -> int:
    """Execute one complete mission through the canonical orchestrator.

    A destination is mandatory: execution creates a real deliverable
    repository, so the operator must say where it lives rather than have one
    appear in the current directory.
    """
    if not destination:
        print(
            "run: --destination is required to execute a mission "
            "(use --plan-only to plan without executing)",
            file=sys.stderr,
        )
        return EXIT_RUN_ERROR
    try:
        client = build_inference_client(settings)
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    runner = _build_mission_runner(settings, client)
    try:
        result = runner.run(objective, destination)
    except MissionRunError as exc:
        print(f"run: mission could not start: {exc}", file=sys.stderr)
        return EXIT_RUN_ERROR
    except (MissionLedgerError, OSError) as exc:
        print(f"run: mission state error: {exc}", file=sys.stderr)
        return EXIT_STATE_ERROR
    print(json.dumps(result.as_dict(), sort_keys=True))
    if not result.awaiting_approval:
        return EXIT_RUN_ERROR
    return EXIT_SUCCESS


def _build_mission_runner(settings: Settings, client: Any) -> MissionRunner:
    """The one mission runtime, shared by `run` and `resume`.

    The recovery budget is the operator's configured semantic retry policy,
    so both entry points bound attempts identically.
    """
    store = MissionStore(settings.state_dir, secret_values=settings.redaction_secrets())
    return MissionRunner(
        client,
        store,
        workspaces_root=settings.state_dir / "workspaces",
        event_sink=_make_event_sink(settings),
        semantic_retries=settings.semantic_retries,
    )


def _cmd_resume(settings: Settings, mission_id: str) -> int:
    """Re-enter one interrupted mission by its durable identity.

    Explicit operator action only: nothing resumes on its own, and a mission
    that already finished with this layer is reported rather than re-run.
    """
    try:
        client = build_inference_client(settings)
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    runner = _build_mission_runner(settings, client)
    try:
        result = runner.resume(mission_id)
    except MissionRunError as exc:
        print(f"resume: {exc}", file=sys.stderr)
        return EXIT_RUN_ERROR
    except StateCorruptionError as exc:
        print(f"resume: mission state unreadable: {exc}", file=sys.stderr)
        return EXIT_STATE_ERROR
    except (MissionLedgerError, OSError) as exc:
        print(f"resume: mission state error: {exc}", file=sys.stderr)
        return EXIT_STATE_ERROR
    print(json.dumps(result.as_dict(), sort_keys=True))
    if result.status in (MISSION_AWAITING_HUMAN_APPROVAL, MISSION_APPROVED):
        return EXIT_SUCCESS
    return EXIT_RUN_ERROR


def _cmd_serve(settings: Settings) -> int:
    """Run the service loop in the foreground with graceful shutdown."""
    # Initialize durable state to prove restart-safe startup (idempotent).
    DurableEvidenceStore(settings.state_dir)
    DurableVerdictStore(settings.state_dir)
    sink = _make_event_sink(settings)
    sink.record(
        Event(
            kind="SERVICE_STARTED",
            producer_role="system",
            details={
                "model_provider": settings.model_provider,
                "model_name": settings.model_name,
            },
        )
    )

    print("Doom Team service ready", flush=True)

    shutdown = threading.Event()

    def _handle_signal(signum: int, frame: object) -> None:
        shutdown.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    while not shutdown.is_set():
        shutdown.wait(timeout=0.5)

    sink.record(Event(kind="SERVICE_STOPPED", producer_role="system", details={}))
    print("Doom Team service stopped", flush=True)
    return EXIT_SUCCESS


def _cmd_status(settings: Settings) -> int:
    """Print operator-readable current state and observability diagnostics."""
    ev_store = DurableEvidenceStore(settings.state_dir)
    verdict_store = DurableVerdictStore(settings.state_dir)
    evidence_count = len(list(ev_store.evidence_dir.glob("*.json")))
    verdict_count = len(list(verdict_store.verdict_dir.glob("*.json")))
    recovery_path = settings.state_dir / "recovery.json"

    sink = _make_event_sink(settings)
    history = EventHistory(sink)
    healthy, health_msg = check_health(settings)
    ready, ready_msg = check_readiness(settings)
    active_tasks = history.active_tasks()
    recent_errors = history.recent_errors()
    last_events = history.last_events()

    print(f"state_dir: {settings.state_dir}")
    print(f"model_provider: {settings.model_provider}")
    print(f"model_name: {settings.model_name}")
    print(f"runtime: {settings.runtime}")
    print(f"semantic_retries: {settings.semantic_retries}")
    print(f"evidence records: {evidence_count}")
    print(f"verdict records: {verdict_count}")
    print(f"recovery state file: {'present' if recovery_path.exists() else 'absent'}")
    print(f"health: {health_msg}")
    print(f"readiness: {ready_msg}")
    print(f"active tasks: {len(active_tasks)}")
    for task in active_tasks:
        print(f"  - {task['task_id']} started at {task['started_at']}")
    print(f"recent errors: {len(recent_errors)}")
    for event in recent_errors:
        print(f"  - {event.kind}: {event.details}")
    print(f"last events: {len(last_events)}")
    for event in last_events:
        print(f"  - {event.timestamp.isoformat()} {event.kind} by {event.producer_role}")
    return EXIT_SUCCESS


def _cmd_doctor(settings: Settings) -> int:
    """Validate configuration, durable state health, and readiness."""
    # Configuration is already validated by ConfigLoader; verify state dir.
    try:
        settings.state_dir.mkdir(parents=True, exist_ok=True)
        probe = settings.state_dir / ".doom_doctor_probe"
        probe.write_text("ok")
        probe.unlink()
    except OSError as exc:
        print(f"doctor: state dir error: {exc}", file=sys.stderr)
        return EXIT_STATE_ERROR

    try:
        DurableEvidenceStore(settings.state_dir)
        DurableVerdictStore(settings.state_dir)
        _make_event_sink(settings)
    except OSError as exc:
        print(f"doctor: state store error: {exc}", file=sys.stderr)
        return EXIT_STATE_ERROR

    healthy, health_msg = check_health(settings)
    ready, ready_msg = check_readiness(settings)
    if not healthy:
        print(f"doctor: {health_msg}", file=sys.stderr)
        return EXIT_STATE_ERROR
    if not ready:
        print(f"doctor: {ready_msg}", file=sys.stderr)
        return 3

    print(f"doctor: {health_msg}")
    print(f"doctor: {ready_msg}")
    return EXIT_SUCCESS


def _cmd_approve(settings: Settings, args: argparse.Namespace) -> int:
    """Record a human approval via the durable ApprovalPort. Never deploys.

    Two shapes of the same decision: ``--mission-id`` approves a completed
    mission (the action and the frontier SHA come from its durable record),
    while the explicit ``--action``/``--frontier-sha`` pair stays available
    for approvals that are not mission runs.
    """
    port = DurableApprovalPort(settings.state_dir, event_sink=_make_event_sink(settings))
    decision = args.decision in ("approve", "true")
    if args.mission_id:
        if args.action or args.frontier_sha:
            # These are derived from the mission record; accepting them
            # silently would let an operator believe they bound the approval
            # to something they named.
            print(
                "approve: --action and --frontier-sha are implied by --mission-id; "
                "pass one form or the other",
                file=sys.stderr,
            )
            return EXIT_APPROVAL_ERROR
        return _cmd_approve_mission(settings, args, port, decision)
    if not args.action or not args.frontier_sha:
        print(
            "approve: provide --mission-id, or both --action and --frontier-sha",
            file=sys.stderr,
        )
        return EXIT_APPROVAL_ERROR
    if args.action.startswith(MISSION_ACTION_PREFIX):
        # Mission decisions go through the ledger, which enforces the gate and
        # serializes deciders. Writing that action directly would record an
        # approval the mission itself never made.
        print(
            f"approve: {MISSION_ACTION_PREFIX!r} actions are decided with --mission-id",
            file=sys.stderr,
        )
        return EXIT_APPROVAL_ERROR
    try:
        digest = port.approve(
            args.action,
            args.frontier_sha,
            args.operator,
            decision=decision,
            contract_revision=args.contract_revision,
            roadmap_revision=args.roadmap_revision,
        )
    except (ValueError, OSError) as exc:
        print(f"approve: failed: {exc}", file=sys.stderr)
        return EXIT_APPROVAL_ERROR
    print(f"approval recorded: {digest}")
    return EXIT_SUCCESS


def _cmd_approve_mission(
    settings: Settings,
    args: argparse.Namespace,
    port: DurableApprovalPort,
    decision: bool,
) -> int:
    """Decide a completed mission by its durable identity. Never deploys.

    Fails closed unless the mission is actually awaiting approval, so a
    blocked mission, one whose final validation failed, one still running,
    and one already decided are all refused.
    """
    store = MissionStore(settings.state_dir, secret_values=settings.redaction_secrets())
    try:
        record = approve_mission(
            store,
            port,
            args.mission_id,
            operator=args.operator,
            decision=decision,
            contract_revision=args.contract_revision,
            roadmap_revision=args.roadmap_revision,
        )
    except MissionLedgerError as exc:
        print(f"approve: {exc}", file=sys.stderr)
        return EXIT_APPROVAL_ERROR
    except StateCorruptionError as exc:
        print(f"approve: mission state unreadable: {exc}", file=sys.stderr)
        return EXIT_STATE_ERROR
    except (ValueError, OSError) as exc:
        print(f"approve: failed: {exc}", file=sys.stderr)
        return EXIT_APPROVAL_ERROR
    print(
        json.dumps(
            {
                "mission_id": record.mission_id,
                "status": record.status,
                "frontier_sha": record.final_frontier_sha,
                "approval": record.approval,
            },
            sort_keys=True,
        )
    )
    return EXIT_SUCCESS


def _cmd_backup(state_dir: Path, backup_dir: Path) -> int:
    """Create a verified backup of durable state."""
    try:
        backup_path = backup_state(state_dir, backup_dir)
    except Exception as exc:  # StateCorruptionError, OSError
        print(f"backup: failed: {exc}", file=sys.stderr)
        return EXIT_STATE_ERROR
    print(f"backup created and verified: {backup_path}")
    return EXIT_SUCCESS


def _cmd_restore(backup_path: Path, state_dir: Path) -> int:
    """Restore durable state from a verified backup."""
    try:
        restore_state(backup_path, state_dir)
    except Exception as exc:  # StateCorruptionError, OSError
        print(f"restore: failed: {exc}", file=sys.stderr)
        return EXIT_STATE_ERROR
    print(f"restore complete from {backup_path}")
    return EXIT_SUCCESS


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for the doom-team console script."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command in ("backup", "restore"):
        state_dir = _resolve_state_dir(args)
        if not state_dir:
            print("configuration error: state_dir is required", file=sys.stderr)
            return EXIT_CONFIG_ERROR
        if args.command == "backup":
            return _cmd_backup(Path(state_dir), Path(args.backup_dir))
        return _cmd_restore(Path(args.backup_path), Path(state_dir))

    try:
        settings = _load_settings(args)
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    if args.command == "resume":
        return _cmd_resume(settings, args.mission_id)
    if args.command == "run":
        return _cmd_run(
            settings,
            args.objective,
            plan_only=args.plan_only,
            destination=args.destination,
        )
    if args.command == "serve":
        return _cmd_serve(settings)
    if args.command == "status":
        return _cmd_status(settings)
    if args.command == "doctor":
        return _cmd_doctor(settings)
    if args.command == "approve":
        return _cmd_approve(settings, args)
    parser.error(f"unknown command: {args.command}")
    return EXIT_CONFIG_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
