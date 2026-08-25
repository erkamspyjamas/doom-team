# Upgrade Runbook

This runbook describes how to upgrade the Doom Team service safely. Upgrades
must never lose or duplicate durable state.

## Principles

- Always create and verify a backup before upgrading.
- Verify the quality gates in a clean environment before upgrading.
- The state directory format must be compatible with the installed version; if
  the format changes, the release notes and ADRs must document the migration.

## Before Upgrading

1. Review the release notes and any new Architecture Decision Records in
   `adr/`.
2. Create a verified backup of the current state:

   ```bash
   doom-team backup --state-dir /var/lib/doom --backup-dir /backups/doom
   ```

3. Confirm the backup verifies (`doom-team doctor` or a `restore` dry run per
   [Disaster Recovery](DISASTER_RECOVERY.md)).

## Procedure

1. Stop the service gracefully (`SIGTERM`).
2. Update dependencies per [Dependency Management](../docs/DEPENDENCIES.md):

   ```bash
   python -m pip install -e ".[dev]" -c constraints.txt
   ```

   or install the new pinned version in a fresh virtual environment.
3. Run the quality gates in a clean environment:

   ```bash
   make lint
   make type
   make test
   ```

4. Verify the installed version and configuration:

   ```bash
   python -c "import doom_team; print(doom_team.__version__)"
   doom-team doctor
   ```

5. Start the service:

   ```bash
   doom-team serve
   ```

6. Confirm health and readiness with `doom-team status`.

## Rolling Back

If the upgrade fails or introduces a regression, roll back by restoring the
verified backup taken before the upgrade and reinstalling the previous version.
See [Rollback](ROLLBACK.md) and [Disaster Recovery](DISASTER_RECOVERY.md).

## After Upgrading

- Update `constraints.txt` if dependencies changed (regenerate with `make lock`).
- Commit any new ADRs and runbook changes.
- Re-run the staging harness and record fresh evidence (see
  [Staging Evidence](../docs/STAGING_EVIDENCE.md)).
