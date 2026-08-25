# Observability Runbook

## Telemetry

`InferenceTelemetryPort.record(endpoint, model, runtime)` records the actual provider/model/runtime route for each inference. Use `InMemoryTelemetry` for tests or implement a durable adapter for production.

Evidence records include producer role/run, command, artifacts, actual model/runtime, timestamp and digest. The `DurableEvidenceStore` persists evidence as JSON files under `evidence/` and copies artifacts under `artifacts/`.

## Verdicts

QA and Reviewer verdicts are persisted via `VerdictStore`. The `DurableVerdictStore` writes JSON files under `verdicts/`.

## Logs

All run outputs and artifacts should be attached to evidence records for later inspection.
