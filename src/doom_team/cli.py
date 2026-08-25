"""Command-line interface for the Doom Team service.

Provides subcommands: serve, status, doctor, approve, backup, restore.

Exit codes (documented in docs/CLI.md):
    0 - success / healthy
    1 - configuration error
    2 - state or store error
    3 - approval failure
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import threading
from collections.abc import Sequence
from pathlib import Path

from doom_team.approval import DurableApprovalPort
from doom_team.backup import backup_state, restore_state
from doom_team.config import ConfigError, ConfigLoader, Settings
from doom_team.evidence import DurableEvidenceStore
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
    approve_parser.add_argument("--action", required=True, help="Approval action")
    approve_parser.add_argument("--frontier-sha", required=True, help="Frontier SHA")
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
    """Record a human approval via the durable ApprovalPort. Never deploys."""
    port = DurableApprovalPort(settings.state_dir, event_sink=_make_event_sink(settings))
    decision = args.decision in ("approve", "true")
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
