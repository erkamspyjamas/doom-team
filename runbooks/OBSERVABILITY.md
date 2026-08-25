# Observability Runbook

## Telemetry

`InferenceTelemetryPort.record(endpoint, model, runtime)` records the actual provider/model/runtime route for each inference. Use `InMemoryTelemetry` for tests or `DurableTelemetry` (in `doom_team.observability`) for production, which writes a `PROVIDER_ROUTE` event to the durable event log.

Evidence records include producer role/run, command, artifacts, actual model/runtime, timestamp and digest. The `DurableEvidenceStore` persists evidence as JSON files under `evidence/` and copies artifacts under `artifacts/`.

## Verdicts

QA and Reviewer verdicts are persisted via `VerdictStore`. The `DurableVerdictStore` writes JSON files under `verdicts/`.

## Durable Event History

All lifecycle, approval, and provider-error events are written to an append-only JSON-lines file at:

```text
<state_dir>/events/events.jsonl
```

Each line is a single JSON object with the following shape:

```json
{
  "timestamp": "2024-01-01T00:00:00+00:00",
  "kind": "TASK_STARTED",
  "producer_role": "dev",
  "producer_run": "run_abc123",
  "details": { ... }
}
```

### Event kinds

| Kind | Producer | Meaning |
|------|----------|---------|
| `MISSION_STARTED` / `MISSION_COMPLETED` | `dev` | Mission lifecycle boundaries |
| `TASK_STARTED` / `TASK_COMPLETED` / `TASK_FAILED` | `dev` | Per-task lifecycle events |
| `PROVIDER_ROUTE` | `inference` | Actual provider/model/runtime used |
| `PROVIDER_ERROR` | `inference` | Inference timeout/overload/rejection/malformed |
| `APPROVAL_RECORDED` / `APPROVAL_DENIED` / `APPROVAL_ERROR` | `approval` | Human approval decisions and failures |
| `SERVICE_STARTED` / `SERVICE_STOPPED` | `system` | Process lifecycle |

## Redaction

Secrets are redacted **at the sink** before persistence. The configured provider API key (and any additional secret values passed to `DurableEventSink`) are replaced with `[REDACTED]` in every string field. Private reasoning contents from inference responses are never recorded; only token counts and provenance are captured.

## Health and Readiness

- `check_health(settings)` returns `(True, "healthy")` when all durable state directories exist and the event log is readable.
- `check_readiness(settings)` returns `(True, "ready")` when healthy and the provider/model/credential configuration is present.

These are wired into the `status` and `doctor` CLI subcommands.

## Operator Diagnostics

Use the `doom-team status` command to see:

- Health and readiness
- Evidence/verdict/recovery counts
- Active tasks (started but not completed)
- Recent errors (provider, approval, task failures)
- Last events

## Retention

The event log is append-only and unbounded by default. Operators should archive or rotate `events.jsonl` according to their retention policy. Evidence and verdict records are separate durable stores and follow the same backup/recovery procedures as the rest of the state directory.
