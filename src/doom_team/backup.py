"""Backup, verify, and restore of the Doom Team durable state.

Produces a verified snapshot of the entire state directory (evidence,
verdicts, recovery, run-id mapping, approvals, preserved workspaces) with a
manifest of per-file sha256 checksums. Restore verifies the backup before
replacing the live state, moving any existing state aside for safety.
"""

from __future__ import annotations

import datetime
import json
import shutil
from pathlib import Path

from doom_team.durable import StateCorruptionError, checksum_hex


def _walk_files(root: Path) -> list[Path]:
    """Return all regular files under root, sorted by relative path."""
    return sorted(p for p in root.rglob("*") if p.is_file())


def backup_state(state_dir: Path, backup_root: Path) -> Path:
    """Copy state_dir into a new timestamped backup under backup_root and verify it."""
    if not state_dir.is_dir():
        raise StateCorruptionError(f"state dir {state_dir} is not a directory")
    backup_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = backup_root / f"doom-backup-{timestamp}"
    if backup_path.exists():
        raise StateCorruptionError(f"backup path already exists: {backup_path}")
    shutil.copytree(state_dir, backup_path, symlinks=True)
    manifest: dict[str, str] = {}
    for file in _walk_files(backup_path):
        rel = str(file.relative_to(backup_path))
        manifest[rel] = checksum_hex(file.read_bytes())
    (backup_path / "BACKUP_MANIFEST.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True)
    )
    if not verify_backup(backup_path):
        raise StateCorruptionError("backup verification failed")
    return backup_path


def verify_backup(backup_path: Path) -> bool:
    """Return True only if the backup has a valid manifest and all files match."""
    manifest_path = backup_path / "BACKUP_MANIFEST.json"
    if not manifest_path.is_file():
        return False
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(manifest, dict):
        return False
    for rel, expected in manifest.items():
        file = backup_path / rel
        if not file.is_file():
            return False
        try:
            actual = checksum_hex(file.read_bytes())
        except OSError:
            return False
        if actual != expected:
            return False
    return True


def restore_state(backup_path: Path, state_dir: Path) -> None:
    """Restore state from a verified backup into state_dir.

    The existing state_dir is moved aside (preserving it) before the backup
    contents are copied into a fresh state_dir.
    """
    if not verify_backup(backup_path):
        raise StateCorruptionError("backup verification failed; refusing to restore")
    if state_dir.exists():
        timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        old = state_dir.with_name(state_dir.name + f".old-{timestamp}")
        state_dir.rename(old)
    state_dir.mkdir(parents=True, exist_ok=True)
    for item in backup_path.iterdir():
        if item.name == "BACKUP_MANIFEST.json":
            continue
        dest = state_dir / item.name
        if item.is_dir():
            shutil.copytree(item, dest)
        else:
            shutil.copy2(item, dest)
