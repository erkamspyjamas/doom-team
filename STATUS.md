# Doom Team — Current Status

Last reviewed: 2026-10-19. This is a verified snapshot, not live monitoring.
Update it with the evidence and [TODO.md](TODO.md) whenever a bounded operation
changes the runtime, its validation or a work item's status.

## Runtime and publication

| Area | Verified state |
|---|---|
| Local runtime baseline | `v0.1.0-rc9`, `c7d98958dc70dbcd4e769a92297aaef5fcfc091e` |
| Latest verified GitHub main/release | RC8, `70fab9e7d9c89330794e90652db82b76dd1e551d`; RC9 publication pending |
| Documentation | STATUS/TODO and navigation updates are a separate documentation-only follow-up to RC9 |
| RC9 complete editable suite | 750 passed; Ruff and strict mypy passed |
| Earlier RC9 affected installed suite | 53 affected approval/supervisor/mission-ledger tests passed |
| RC9 complete installed suite | 750 passed in a frozen RC9 snapshot; actual offline wheel build/install passed; see `BASE-01` |
| Models | DeepSeek V4.1 Flash planner/author; independent GLM 5.3 Flash QA/Reviewer; high reasoning |

The installed CLI uses the current local checkout through its editable
installation. Publication state and the runtime baseline are listed separately
so an unpublished local fix is not mistaken for a pushed release. Inspect them
with `git log -1`, `git show v0.1.0-rc9 --stat` and `git ls-remote origin`.

## Demonstrated behavior

- Natural-language planning, capability routing, isolated Git workspaces,
  out-of-process Gas City execution, validation, QA, Reviewer, bounded retries,
  fresh final validation and explicit human approval work end to end.
- The latest real-repository usage feature completed 3/3 tasks in four attempts
  with zero in-run operator code edits. The CLI integration and documentation
  tasks passed their first attempts; aggregation required one correction.
- That candidate passed 36 independent acceptance checks, 746 external tests,
  746 fresh final installed tests, Ruff and mypy. Human approval bound the exact
  candidate SHA before its fast-forward integration and RC8 push.
- `doom-team usage` now reports tokens/costs by actual provider/model offline.
  Its demonstrated pilot had 11 successful calls and known cost USD 0.05091098,
  with no provider errors. This is that state directory's cost, not total
  historical project spending. See [usage reporting](runbooks/USAGE.md).
- RC9 preserves the verified digest when an approval record is loaded and
  serialized for audit. Existing approval records and decisions are unchanged.

The selected profile passed all ten frozen benchmark cases on RC4 across two
batches: four selection cases and six remaining cases, with 27/27 tasks
integrated. This is historical profile-selection evidence, not a fresh RC9
benchmark, a ten-case holdout, or a general real-repository reliability rate.

## Current limits

- The active MissionRunner executes ready tasks serially. Capability routing
  does not imply concurrent execution; the parallel target is `PAR-01`.
- Context selection is a deterministic task-focused heuristic. Only complete
  files up to 50000 characters are included within a 60000-character total
  context bound. An existing omitted file cannot be replaced from guesses.
  Larger required edits may block; see `CTX-01` and
  [the context contract](runbooks/INSTALL.md#task-workspace-context).
- Candidate wheel/venv isolation prevents stale package imports. It is not an
  OS filesystem/process sandbox; containment work is tracked as `ISO-01`.
- Crash/resume has unit coverage; the broader real-process exercise is
  `RES-01`. One successful self-development feature does not close all
  reliability and production-readiness goals in [ROADMAP.md](ROADMAP.md).

## Evidence index

These are retained operator-host artifacts under `/home/eko/doom`; they are not
bundled into the product repository or wheel. Older reports describe their
state at the time. Later approval/publication records supersede those state
labels without rewriting the historical reports.

| Evidence | Operator-host location | Scope |
|---|---|---|
| RC9 fix | `pilots/doom-team-approval-digest-rc9-20261019/REPORT.md` | 750 editable tests, 53 installed affected tests, preserved canonical approval replay |
| RC9 full installed baseline | `pilots/doom-team-rc9-installed-baseline-20261019/quality/verification.json` | Authoritative outcome for `BASE-01`; snapshot is immutable RC9, separate from documentation editing |
| Real usage pilot | `pilots/doom-team-usage-rc7-20261019/VERIFICATION.md` | Independent acceptance and per-task/final evidence before human approval |
| Approval and integration | `pilots/doom-team-usage-release-rc8-20261019/REPORT.md` | Explicit human approval, exact-SHA integration, installed CLI checks |
| RC8 push | `pilots/doom-team-rc8-push-20261019/REPORT.md` | Atomic main/tag push and remote-ref verification |
| Historical model evaluation | `benchmarks/doom-bench-v1/experiments/rc4-selected-remaining-20261019/summary.json` | Selected-profile coverage across the two RC4 batches |

For the next operation and measurable completion criteria, use
[the prioritized work list](TODO.md).
