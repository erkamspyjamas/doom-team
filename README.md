# Doom Team

Doom Team is a standalone engineering runner: a natural-language objective
becomes a task plan, Gas City workers implement changes, and validation,
independent QA and Reviewer checks lead to an explicit human approval gate.
It has no runtime dependency on the older Doom Autopilot service.

For current work, start with:

- [STATUS.md](STATUS.md): verified runtime baseline, evidence and known limits.
- [TODO.md](TODO.md): prioritized work with completion criteria.
- [Installation](runbooks/INSTALL.md) and [operation](runbooks/OPERATE.md).

For the design and target architecture, read:

1. `PROJECT.md`
2. `ARCHITECTURE.md`
3. `ROADMAP.md`
4. `ACCEPTANCE.md`

Also review the Architecture Decision Records in `adr/` for key decisions.

`ROADMAP.md` describes architectural goals; it is not a completed-work checklist.

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

## Native technology stacks

New operator-console jobs let the team choose the implementation stack and
dependencies. CLI operators can select `DOOM_VALIDATION_POLICY=project` for the
same native setup/build/check/test workflow. The runner using Python 3.11 does
not constrain the project language. See [project validation](runbooks/PROJECT_VALIDATION.md).
