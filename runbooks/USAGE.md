# Usage Reporting

`doom-team usage` is a read-only, offline operator command that summarizes
provider usage events in a state directory. The full field reference, exit
codes, and limitations live in
[OPENROUTER.md](OPENROUTER.md#reporting-usage-and-cost-offline).

## Usage

```
doom-team usage [--state-dir PATH] [--json]
doom-team --state-dir PATH usage [--json]
```

`--state-dir` may be supplied before or after the subcommand. An explicit
`--state-dir` always takes precedence over `DOOM_STATE_DIR`. If neither is
provided, the command exits 1.

## Exit codes

- `0`: report produced (including an existing state directory with no
  `events/events.jsonl`)
- `1`: no state directory selected
- `2`: selected state is missing/not a directory, or the event log cannot be
  aggregated (malformed JSON, invalid counters/costs, unreadable file)

## Output

`--json` emits a single strict JSON object with `schema_version: 1` and the
aggregate fields plus `by_model`. `by_model` is sorted deterministically by
`(provider, model)`. Unknown cost/reasoning values are `null`, never silently
zero. `known_cost_usd` is `null` when no costs were reported.

Default text output labels known cost, missing-cost counts, tokens, errors,
fallbacks, and per-model usage. It never prints prompts, reasoning text,
credentials, raw provider responses, or arbitrary event fields.

## Limitations

- Summarizes ALL `PROVIDER_USAGE` events in the selected state directory; it is
  not mission-filtered.
- Counts `PROVIDER_ERROR` and `PROVIDER_FALLBACK` events separately; it does not
  estimate charges for failed calls.
- Does not infer QA versus Reviewer attribution.
- Reads `<state>/events/events.jsonl` directly. It creates no stores, files, or
  directories and makes no network calls.
- Ignores unrelated legacy event kinds and blank lines.
