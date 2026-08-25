# Doom Team Operability Contract

This document defines the service-level operability contract for running Doom Team as a durable service. It complements ADR-0007 and is pinned by executable tests in `tests/test_operability.py`.

## 1. Process Lifecycle

- Startup initializes all adapters (workspace, repository, evidence store, verdict store, recovery controller, telemetry, orchestration adapter) from their configured durable state directories.
- Startup must be idempotent: re-running the service must not lose or duplicate state.
- Shutdown must be graceful and must never destroy preserved, failed, dirty, or uncertain workspaces.

## 2. Durable State Ownership

The service owns the following state and must create it if missing, never write outside it, and support backup/recovery:

| State | Adapter | Notes |
|-------|---------|-------|
| Evidence records | `DurableEvidenceStore` | JSON under `<base>/evidence/`; artifacts under `<base>/artifacts/` |
| Verdict records | `DurableVerdictStore` | JSON under `<base>/verdicts/` |
| Recovery state | `RecoveryController` | JSON file with semantic retry budget and exhaustion |
| Orchestration run mapping | `GasCityAdapter` | JSON file mapping Doom run ids to Gas City run ids |
| Preserved workspaces | `GitWorkspace` | Moved under `<base>/preserved/` |
| Event history | `DurableEventSink` | Append-only JSON-lines under `<base>/events/events.jsonl` |

## 3. Configuration Ownership and Precedence

- Configuration is owned by operators.
- Precedence: explicit runtime overrides (CLI/env) > config file > defaults.
- Secrets must never be embedded in config files, logs, or evidence.
- Actual provider/model/runtime must be recorded via `InferenceTelemetryPort` for every inference.

## 4. Human Approval Boundary

Irreversible actions require a positive human approval at runtime:

- push
- merge
- deploy
- destructive DB
- credential provisioning

`ApprovalBoundary` returns `False` when no `ApprovalPort` is provided (fail-closed). `Supervisor.decide` returns `READY_FOR_FINAL_APPROVAL` without an approval port, `HUMAN_REQUIRED` when approval is denied, and `RELEASE_AUTHORIZED` only after a positive approval.

## 5. Restart Semantics and Idempotency

Must survive restart:
- Semantic retry exhaustion (`RecoveryController`)
- Orchestration run mappings (`GasCityAdapter`)
- Evidence and verdict records (durable stores)
- Preserved workspaces (`GitWorkspace`)
- Event history (`DurableEventSink`)

Must never double-apply:
- A candidate commit
- A semantic retry consumption (registering the same blocker twice for the same attempt does not double-consume)
- A release (requires fresh evidence and a positive approval)

Runtime retries never consume the semantic budget.

## 6. Health and Readiness

- `healthy`: process is alive and can serve basic operations.
- `ready`: all durable state is loaded and the service can accept and execute work.
- These must be reported distinctly and never conflated with acceptance. A run being `completed` in orchestration does not imply the candidate is accepted.

## 7. Irreversible Actions (Human-Only)

The service must never autonomously perform:

- `PUSH`
- `MERGE`
- `DEPLOY`
- `DESTRUCTIVE_DB`
- Credential provisioning

The permission engine returns `REQUIRES_APPROVAL` for all roles for these capabilities. There is no autonomous capability for credential provisioning.
