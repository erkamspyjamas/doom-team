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
| `PROVIDER_FALLBACK` | `inference` | Primary inference route unavailable; the single fallback route was used |
| `PROVIDER_ERROR` | `inference` | Inference timeout/overload/rejection/malformed |
| `APPROVAL_RECORDED` / `APPROVAL_DENIED` / `APPROVAL_ERROR` | `approval` | Human approval decisions and failures |
| `SERVICE_STARTED` / `SERVICE_STOPPED` | `system` | Process lifecycle |

### Filtered event queries: `EventHistory.query`

`EventHistory.query` is a read-only, in-process API that returns a task's recent
events as parsed `Event` objects, so operators and diagnostics do not have to
read or parse `events.jsonl` themselves. The existing summaries
(`active_tasks()`, `recent_errors()`, `last_events()`) are unchanged and remain
what `doom-team status` prints.

Signature (all arguments are keyword-only):

```python
EventHistory.query(
    *,
    limit: int = 10,
    kinds: Sequence[str] | None = None,
    task_id: str | None = None,
    producer_role: str | None = None,
) -> list[Event]
```

`Sequence` here means a `list` or a `tuple`.

#### Usage example

```python
from doom_team.observability import DurableEventSink, EventHistory

history = EventHistory(DurableEventSink("<state_dir>"))

# Last 5 TASK_STARTED/TASK_FAILED events for task-a produced by dev.
events = history.query(
    limit=5,
    kinds=("TASK_STARTED", "TASK_FAILED"),
    task_id="task-a",
    producer_role="dev",
)
for event in events:
    print(event.timestamp.isoformat(), event.kind, event.details.get("task_id"))

# Defaults: the last 10 events of any kind from any producer.
recent = history.query()

# An empty kind sequence matches nothing, so this returns [].
nothing = history.query(kinds=[])
```

#### Filters

Filters are optional and combine with AND; omitting one (`None`) imposes no
constraint on that field.

| Argument | Matches | Notes |
|----------|---------|-------|
| `kinds` | Exact, case-sensitive membership in `Event.kind` | `None` = no kind filter. An empty list/tuple matches nothing. Duplicate kinds are allowed and never duplicate results. |
| `task_id` | Exact, nonempty string in `details["task_id"]` | Events whose details have no `task_id`, or a non-string `task_id`, do not match. |
| `producer_role` | Exact `Event.producer_role` | `None` = no producer filter. |

Matching never trims, case-folds, or normalizes: a whitespace-only `task_id` is
a valid value and matches only that exact string. `kinds` values such as
`"task_started"` do not match `TASK_STARTED`.

#### Selection and ordering

- The limit is applied **after** filtering: `query(limit=5, ...)` returns the
  last 5 events that match, not the matches that happen to fall inside the last
  5 events.
- Results keep their original durable append order (oldest first within the
  returned window). They are never sorted by timestamp, so tied or
  non-monotonic UTC timestamps do not change the answer — append order wins.
- `limit` defaults to 10. `limit=0` returns `[]`. A `limit` larger than the
  match count returns every match.

#### Validation

Arguments are validated before the log is read, so the same errors are raised
with `limit=0` and when the event log is empty or absent.

| Argument | `TypeError` | `ValueError` |
|----------|-------------|--------------|
| `limit` | Not an `int`, or a `bool` | Negative |
| `kinds` | Not `None`/`list`/`tuple` (a bare `str`, `bytes`, `dict`, `set`, `int`, ...), or an item that is not a `str` | An item that is the empty string |
| `task_id` | Not `None` and not a `str` | The empty string |
| `producer_role` | Not `None` and not a `str` | The empty string |

#### Read-only behavior and reopen

Querying is read-only: it does not append, rewrite, compact, or reorder
`events.jsonl`, and it creates no files or directories. Repeated calls against
the same state return identical results, and reopening the durable sink (for
example in a later process) reads the same bytes and yields the same events.

#### No CLI command

There is no CLI subcommand for filtered queries; the API above is the supported
interface. `doom-team status` continues to print the built-in summaries, and
`doom-team usage` reports provider usage aggregates.

## Redaction

Secrets are redacted **at the sink** before persistence. The configured provider API key (and any additional secret values passed to `DurableEventSink`) are replaced with `[REDACTED]` in every string field. Private reasoning contents from inference responses are never recorded; only token counts and provenance are captured.

### Architecture: pure helper plus thin adapter

Event-value redaction is implemented by the pure `redact_value(value, secret_values)` helper in `src/doom_team/redaction.py`. The helper has no dependencies beyond the standard library and performs no I/O: it reads no environment variables, configuration or files, and writes no logs or network traffic. It walks strings, dict values, lists and tuples, returning new containers and never mutating its input.

`RedactingEventSink._redact` is a thin delegating adapter: it calls `redact_value(value, self._secrets)` and contains no traversal logic of its own. Its signature, its callers and the sink's observable output are unchanged.

Redaction behavior and policy are unchanged by this extraction. Strings still replace each nonempty configured secret in the supplied order with `[REDACTED]`; there is no sorting, de-duplication, key redaction, regex matching or added normalization. Overlapping secrets, duplicate or empty secrets and literal `[REDACTED]` values behave exactly as before. Dict keys stay unchanged while values are recursively processed, dict/list/tuple outputs keep their built-in container types and ordering, and other values pass through unchanged. `RedactingEventSink` still filters empty secrets in constructor order, and `record()` still preserves kind/role/run/timestamp while emitting the same redacted `Event` to the wrapped sink, so the persisted JSONL, reload and restart behavior match the previous baseline exactly.

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

For a narrower, filtered view of a single task's recent events, call
`EventHistory.query` from Python as shown above; no CLI command exposes it.

## Retention

The event log is append-only and unbounded by default. Operators should archive or rotate `events.jsonl` according to their retention policy. Evidence and verdict records are separate durable stores and follow the same backup/recovery procedures as the rest of the state directory.
