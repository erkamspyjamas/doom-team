# Doom Team

First real project developed by Doom Autopilot.

Read in order:
1. `PROJECT.md`
2. `ARCHITECTURE.md`
3. `ROADMAP.md`
4. `ACCEPTANCE.md`

Also review the Architecture Decision Records in `adr/` for key decisions.

The repository intentionally starts with documentation only. Implementation must be produced by the Autopilot.

## Operator usage reporting

`doom-team usage` is a read-only, offline command that summarizes the
provider cost and token consumption recorded in a state directory:

```bash
doom-team usage --state-dir /absolute/state
doom-team usage --state-dir /absolute/state --json
doom-team --state-dir /absolute/state usage --json
```

`--state-dir` may be given before or after the `usage` subcommand and always
takes precedence over `DOOM_STATE_DIR`. The command needs no provider
settings or credentials and makes no network calls: it reads
`<state>/events/events.jsonl` directly, creates no stores/files/directories,
and leaves the state unchanged. Totals aggregate **all** events in the
selected state directory and are not mission-filtered.

See `runbooks/USAGE.md` for the command reference and exit codes, and
`runbooks/OPENROUTER.md` for the `schema_version: 1` JSON fields, the
null/unknown cost and reasoning semantics, and how usage events are produced.
