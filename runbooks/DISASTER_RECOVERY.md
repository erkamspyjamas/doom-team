# Disaster Recovery Runbook

This runbook documents the durable-state lifecycle: backup, restore, and
corruption handling. All durable stores (evidence, verdicts, recovery state,
run-id mapping, approvals) use atomic writes (write-temp-fsync-rename) and
embed checksums. Any load that encounters a missing, malformed, or
checksum-mismatched file fails closed with `StateCorruptionError`; it is
never silently treated as valid empty/default state.

## CLI state directory precedence

For every command that reads or mutates durable state, an explicit
`--state-dir` flag — whether given before the subcommand
(`doom-team --state-dir /var/lib/doom backup ...`) or after it
(`doom-team backup --state-dir /var/lib/doom ...`) — always wins over the
`DOOM_STATE_DIR` environment variable. The environment variable is used only
when no CLI value is supplied. The examples below use the documented syntax.

## Stale temp files from a crash

All durable writes are atomic: the service writes to a unique temporary file
in the same directory, fsyncs it, and atomically renames it over the
destination. If the process crashes between creating the temp file and the
rename, a unique temp file (named `<destination>.<random>.tmp`) may remain.
It never blocks future writes (each subsequent write uses a fresh unique
name), is never read as destination state, and is never automatically deleted
by the service. Such files are harmless; operators may remove them manually
if desired.

## Backup

Create a verified snapshot of the entire state directory:

```bash
doom-team backup --state-dir /var/lib/doom --backup-dir /backups/doom
```

The command copies the state directory into a new timestamped directory
(`doom-backup-<UTC-timestamp>`) and writes a `BACKUP_MANIFEST.json`
containing the sha256 of every file. The backup is verified before the
command returns successfully.

## Restore

Restore from a verified backup:

```bash
doom-team restore --state-dir /var/lib/doom --backup-path /backups/doom/doom-backup-20250101T000000Z
```

The backup is verified first; if verification fails, nothing is restored.
The existing state directory is moved aside to `state_dir.old-<timestamp>`
for safety, and the backup contents are copied into a fresh state directory.

## Restart Recovery

The service re-opens all durable stores on startup. Because writes are
atomic and checksummed, a crash mid-write never leaves a half-written file.
A corrupt file is detected immediately and raises `StateCorruptionError`
rather than being silently reset, so operators can restore from the most
recent verified backup.

## Corrupt State Handling

If startup or `doctor` reports corruption:

1. Stop the service.
2. Inspect the affected files (e.g., `evidence/*.json`, `verdicts/*.json`,
   `recovery.json`, `store.json`, `approvals/*.json`).
3. Restore from the most recent verified backup:
   ```bash
   doom-team restore --state-dir /var/lib/doom --backup-path <latest-backup>
   ```
4. Verify the restored state with `doom-team doctor`.
5. Restart the service.

Never delete a corrupt file to "reset" state unless you have confirmed via
backup verification that it is safe to discard that state.
