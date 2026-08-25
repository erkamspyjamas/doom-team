# Operate Runbook

This runbook covers day-to-day operation of the Doom Team service as a durable
process. It complements the [Operability Contract](../docs/OPERABILITY.md) and
[Install](INSTALL.md).

## Prerequisites

- A valid configuration per the [Configuration Reference](../docs/CONFIGURATION.md).
- The required credential environment variable set (e.g. `DOOM_API_KEY`).
- A reachable, durable state directory.
- State directory precedence: an explicit `--state-dir` CLI flag (in either
  flag position) always wins over the `DOOM_STATE_DIR` environment variable;
  the environment variable is used only when no CLI value is supplied.

## Start

```bash
export DOOM_STATE_DIR=/var/lib/doom
export DOOM_MODEL_PROVIDER=nvidia
export DOOM_MODEL_NAME=deepseek-v4
export DOOM_[REDACTED]   # never commit or log this value
doom-team serve
```

Startup is idempotent: all durable stores (evidence, verdicts, recovery,
run-id mapping, event history) are initialized or re-opened from the state
directory. Re-running the service never destroys or duplicates existing state.

## Graceful Shutdown

Send `SIGTERM` (or `SIGINT`) to the service process. The service records a
`SERVICE_STOPPED` event and exits with status 0. Shutdown never destroys
preserved, failed, dirty, or uncertain workspaces.

## Health and Readiness

- `healthy`: the process is alive, all durable state directories exist, and the
  event log is readable.
- `ready`: healthy plus the model provider/name and credential environment
  variable are configured.

These are distinct concepts and must never be conflated with acceptance: a run
being `completed` in orchestration does not imply the candidate is accepted.

```bash
doom-team status
```

reports both health and readiness along with counts of evidence, verdicts,
recovery state, active tasks, recent errors, and last events.

## Diagnostics

```bash
doom-team doctor
```

validates configuration, durable state, health, and readiness. Exit codes:

- `0` success / healthy
- `1` configuration error
- `2` state or store error
- `3` readiness / approval failure

## Durable State

The service owns the state directory it is configured with and must never write
outside it:

| State | Location |
|-------|----------|
| Evidence records | `<state_dir>/evidence/` |
| Artifacts | `<state_dir>/artifacts/` |
| Verdict records | `<state_dir>/verdicts/` |
| Recovery state | `<state_dir>/recovery.json` |
| Run-id mapping | `<state_dir>/store.json` |
| Preserved workspaces | `<state_dir>/preserved/` |
| Event history | `<state_dir>/events/events.jsonl` |
| Approval records | `<state_dir>/approvals/` |

Create verified backups regularly (see [Disaster Recovery](DISASTER_RECOVERY.md)).

## Event Log Retention

The event log is append-only and unbounded by default. Archive or rotate
`<state_dir>/events/events.jsonl` per your retention policy.

## Common Operational Tasks

- **Check the semantic recovery budget**: `doom-team status` shows whether
  `recovery.json` is present; inspect the file for per-task
  `semantic_retries_left` and `exhausted`. Exhaustion survives restart.
- **Inspect preserved workspaces**: failed/dirty workspaces are moved under
  `<state_dir>/preserved/` for forensic analysis and are never deleted.
- **Review approvals**: approval records live under `<state_dir>/approvals/`
  and are bound to a frontier SHA and contract/roadmap revisions.
- **Review the actual provider/model/runtime route**: `PROVIDER_ROUTE` events
  are recorded in the event log for every inference.

## Troubleshooting

- Service fails to start: run `doom-team doctor` and check configuration
  precedence in the [Configuration Reference](../docs/CONFIGURATION.md).
- Corrupt durable state: `doctor` reports a `StateCorruptionError`; restore from
  the most recent verified backup (see [Disaster Recovery](DISASTER_RECOVERY.md)).
- Provider or Gas City outage: transient runtime failures never consume the
  semantic budget; see [Recovery](RECOVERY.md).
