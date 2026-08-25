# Rollback Runbook

This runbook describes how to roll back the Doom Team service or a released
candidate. Rollback of a release is an irreversible/shared-state operation and
is **human-gated**: it must never be performed by the autonomous path.

## Scope

- **Service rollback**: reverting the installed software to a previous version.
- **State rollback**: restoring durable state from a verified backup.
- **Release rollback**: reverting a deployed candidate (human-only).

## Preconditions

- A verified backup exists (see [Disaster Recovery](DISASTER_RECOVERY.md)).
- For release rollback, a positive human approval is recorded through the
  `ApprovalPort` before any mutation.

## Service Rollback

1. Stop the service gracefully.
2. Install the previous version (see [Dependency Management](../docs/DEPENDENCIES.md)).
3. Run `doom-team doctor` to confirm the state is compatible.
4. Restart and verify with `doom-team status`.

## State Rollback

1. Stop the service.
2. Restore from the most recent verified backup:

   ```bash
   doom-team restore --state-dir /var/lib/doom --backup-path <latest-backup>
   ```

   The existing state directory is moved aside (preserved), never deleted.
3. Verify with `doom-team doctor`.
4. Restart the service.

## Release Rollback

Releasing and reverting a release are human-only operations. A rollback of a
deployed candidate:

1. Is only performed after a positive human approval.
2. Uses the same approval boundary as deployment.
3. Records an approval event in the durable event log.

The autonomous path never performs release or rollback mutations.
