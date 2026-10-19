# Doom Team — Current Status

Last reviewed: 2026-10-19. This is a verified snapshot, not live monitoring.
Update it with the evidence and [TODO.md](TODO.md) whenever a bounded operation
changes the runtime, its validation or a work item's status.

## Runtime and publication

| Area | Verified state |
|---|---|
| Local runtime baseline | `v0.1.0-rc12`, `8ebee8a785fd139d5574a159a2edc90a2b288613`; approved complete PROOF-03 refactor/fix |
| Latest verified GitHub publication | RC9 tag at `c7d98958dc70dbcd4e769a92297aaef5fcfc091e`; main verified at `5209205b50812b8241627ae0a69e4f0a5c869992`; RC10/RC11/RC12 publication pending |
| Documentation | This status/work-list update follows the immutable RC12 runtime tag |
| RC9 complete editable suite | 750 passed; Ruff and strict mypy passed |
| Earlier RC9 affected installed suite | 53 affected approval/supervisor/mission-ledger tests passed |
| RC9 complete installed suite | 750 passed in a frozen RC9 snapshot; actual offline wheel build/install passed; see `BASE-01` |
| RC10 installed validation | 760 external installed tests and 760 fresh final installed tests passed; actual wheel builds/installs, Ruff and strict mypy passed |
| RC11 installed validation | 799 external installed tests and 799 fresh final installed tests passed; actual wheel builds/installs, Ruff and strict mypy passed |
| RC12 installed validation | 2272 external checks, 853 external installed tests and 853 fresh final installed tests passed; real wheel builds/installs, Ruff and strict mypy passed |
| Models | DeepSeek V4.1 Flash planner/author; independent GLM 5.3 Flash QA/Reviewer; high reasoning |

The installed CLI uses the current local checkout through its editable
installation. Publication state and the runtime baseline are listed separately
so local changes are distinguished from verified GitHub refs. Publication
records below describe their recorded remote snapshots; newer local releases
remain unpublished until separately authorized. Inspect them with `git log -1`,
`git show v0.1.0-rc12 --stat` and `git ls-remote origin`.

## Demonstrated behavior

- PROOF-03 extracted event-value redaction into a pure helper. The first run
  passed registered gates but was blocked by additional review on a string
  subclass regression. A fresh autonomous follow-up corrected it; the complete
  final candidate passed 2272 independent checks and both 853-test installed
  suites before explicit human approval and local RC12 integration. Both runs
  are retained: four attempts, 14 successful calls, known USD 0.02821515 total.
  The follow-up alone passed its first attempt; the refactor was not a
  first-pass success. No operator candidate-code edits occurred.

- PROOF-02 added `EventHistory.query` with exact conjunctive kind/task/role
  filters and a limit applied after matching in durable append order. Both
  tasks passed on first attempts; 107 independent installed API checks,
  799 external installed tests and 799 fresh final installed tests passed.
  Known cost USD 0.01770005 for seven calls; no provider errors or operator
  code edits. The exact candidate was explicitly approved and integrated
  locally as RC11; existing history behavior remains unchanged.

- PROOF-01 fixed active-task diagnostics hiding running retries/restarts. One
  task passed in one attempt, with 55 independent installed acceptance checks,
  760 external installed tests and 760 fresh final installed tests. Known cost
  USD 0.01149598 for four calls; zero provider errors or operator code edits.
  Explicit human approval bound the exact SHA before local RC10 integration.
  This is one bounded diagnostics defect, not a general reliability rate.
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
- RES-01 demonstrated actual SIGKILL/new-process resume, preserved plan and
  retry budgets, no duplicated integration, and terminal resume with no work.
  Its first overall result stayed blocked because an unchanged operator-made
  fixture failed Ruff; the corrected-fixture repeat is being prepared under
  `pilots/doom-team-res01-process-resume-v2-20261019/`. Keep RES-01 open until
  every registered gate passes. These bounded cases do not close broader
  reliability and production-readiness goals in [ROADMAP.md](ROADMAP.md).

## Evidence index

These are retained operator-host artifacts under `/home/eko/doom`; they are not
bundled into the product repository or wheel. Older reports describe their
state at the time. Later approval/publication records supersede those state
labels without rewriting the historical reports.

| Evidence | Operator-host location | Scope |
|---|---|---|
| RES-01 first exercise | `pilots/doom-team-res01-process-resume-20261019/VERIFICATION.md` | Actual process recovery passed; overall blocked by operator fixture/preflight error; retained unchanged |
| RES-01 corrected repeat | `pilots/doom-team-res01-process-resume-v2-20261019/README.md` | New fixture, full offline preflight, frozen effective lint settings; result pending |
| PROOF-03 initial review | `pilots/doom-team-proof03-redaction-refactor-20261019/VERIFICATION.md` | Original registered passes plus retained supplemental review block; original mission unapproved |
| PROOF-03 follow-up | `pilots/doom-team-proof03-redaction-fix-20261019/VERIFICATION.md` | Complete final candidate, new regressions, independent checks and both-run history |
| RC12 approval/integration | `pilots/doom-team-proof03-release-rc12-20261019/REPORT.md` | Final composite approval, exact-SHA fast-forward and immutable local RC12; no push |
| PROOF-02 pilot | `pilots/doom-team-proof02-history-query-20261019/VERIFICATION.md` | Exact-SHA API preservation, independent installed acceptance and all outcomes |
| RC11 approval/integration | `pilots/doom-team-proof02-release-rc11-20261019/REPORT.md` | Explicit approval, exact-SHA fast-forward and immutable local RC11; no push |
| PROOF-01 pilot | `pilots/doom-team-proof01-active-retry-20261019/VERIFICATION.md` | Baseline defect reproduction, exact-SHA independent acceptance and all outcomes |
| RC10 approval/integration | `pilots/doom-team-proof01-release-rc10-20261019/REPORT.md` | Explicit human approval, exact-SHA fast-forward, immutable local RC10 tag; no push |
| RC9 fix | `pilots/doom-team-approval-digest-rc9-20261019/REPORT.md` | 750 editable tests, 53 installed affected tests, preserved canonical approval replay |
| RC9 full installed baseline | `pilots/doom-team-rc9-installed-baseline-20261019/quality/verification.json` | Authoritative outcome for `BASE-01`; snapshot is immutable RC9, separate from documentation editing |
| Real usage pilot | `pilots/doom-team-usage-rc7-20261019/VERIFICATION.md` | Independent acceptance and per-task/final evidence before human approval |
| Approval and integration | `pilots/doom-team-usage-release-rc8-20261019/REPORT.md` | Explicit human approval, exact-SHA integration, installed CLI checks |
| RC8 push | `pilots/doom-team-rc8-push-20261019/REPORT.md` | Atomic main/tag push and remote-ref verification |
| RC9 push | `pilots/doom-team-rc9-push-20261019/REPORT.md` | Atomic main/immutable RC9 tag push, publication-status follow-up and independent remote-ref verification |
| Historical model evaluation | `benchmarks/doom-bench-v1/experiments/rc4-selected-remaining-20261019/summary.json` | Selected-profile coverage across the two RC4 batches |

For the next operation and measurable completion criteria, use
[the prioritized work list](TODO.md).
