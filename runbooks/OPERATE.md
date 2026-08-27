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

## The command line

Every command below is the installed `doom-team` console script from the
runtime virtual environment created in [Install](INSTALL.md):

```bash
.venv/bin/doom-team <subcommand>
```

Activating the virtual environment (`. .venv/bin/activate`) puts `doom-team` on
`PATH` so the prefix can be dropped. Both forms use the same interpreter.

`python -m doom_team.cli` is a development convenience for running from a
checkout; it is not the operator entrypoint, and it must never be combined with
a hand-set `PYTHONPATH` to stand in for an installation.

## Mission lifecycle

A mission takes one natural-language objective and a destination repository.
Doom Team plans it, routes each task to a specialist, executes the tasks, and
stops at the human approval gate — it never pushes, deploys, or publishes.

```bash
.venv/bin/doom-team run "<objective>" --destination /path/to/new/repo
```

`--destination` is required unless `--plan-only` is given. `--plan-only` plans
and routes the objective without executing any work. On success the command
prints a JSON object containing the `mission_id` and a status of
`awaiting_human_approval`.

Resume an interrupted mission from durable state — a killed process, a reboot —
by its durable identity:

```bash
.venv/bin/doom-team resume <mission_id>
```

Record the human decision on a mission that reached the approval gate. The
mission id implies the action and frontier SHA, so they are not passed
separately:

```bash
.venv/bin/doom-team approve --mission-id <mission_id> \
  --operator <who> --decision approve
```

`--decision deny` records a refusal. Approval records the decision and nothing
else: it never pushes, merges, deploys, or creates a remote.

## Gas City task execution

Task attempts run either inside the service process or as real Gas City work
items. This is an explicit choice, never inferred from whether `gc` happens to
be installed:

```bash
export DOOM_TASK_TRANSPORT=gas_city
export DOOM_GAS_CITY_BINARY=/absolute/path/to/gc
export DOOM_GAS_CITY_CITY=/absolute/path/to/doom-team-city
```

See the [Configuration Reference](../docs/CONFIGURATION.md#task-execution-transport)
for every setting and its default. The city is created on first use if the path
does not exist; Doom Team owns it and keeps it separate from any other city.

**Interpreter contract.** Each attempt is executed by a separate worker process
that Doom Team launches as `sys.executable -m doom_team.worker` — the *same*
interpreter running the CLI. That worker runs with its working directory set to
a per-attempt job directory, and it re-reads the same `DOOM_*` configuration
from the environment it inherits. Two consequences:

- The interpreter running `doom-team` must have the package installed. A source
  checkout that is importable only because of the current directory, or because
  of a relative `PYTHONPATH`, will fail in the worker with
  `ModuleNotFoundError: No module named 'doom_team'`.
- The provider credential and the other `DOOM_*` settings must be present in
  the environment you start `doom-team` from, because the worker inherits them.

Job artifacts are runtime state and live under `<state_dir>/gas-city-jobs/`,
never inside the deliverable repository.

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
