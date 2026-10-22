# Doom Team — Current Status

Last publication check: 2026-10-22. Runtime evidence below is the 2026-10-19
snapshot unless explicitly dated otherwise. This is not live monitoring.
Update it with the evidence and [TODO.md](TODO.md) whenever a bounded operation
changes the runtime, its validation or a work item's status.

## Public repository check — 2026-10-22

- The repository is public at https://github.com/erkamspyjamas/doom-team.
  The default branch is now `main`, replacing the legacy `openclaw/autopilot`
  landing branch. Legacy branches and existing tags were retained.
- Main through `1f2f7cbe1ad53da20f162244df2c8e2c1c2815f0` was pushed and
  verified against the remote ref. Anonymous GitHub API/raw access confirmed
  public visibility and byte-for-byte README equality. This includes the
  previously local RC14 changes and native project policy; the RC14 tag itself
  was not pushed by this operation. The older publication rows below remain
  historical evidence, not the current main ref.
- The new README covers the engine, portable setup, model roles, native
  validation, recovery, usage, human decisions, and actual limits. Its 31 local
  links/anchors and six Bash blocks passed structural/syntax checks. The example
  configuration and manifest passed the installed engine's parsers without
  provider calls or generated-code execution. `.gitignore` now excludes local
  credentials, virtual environments, caches, builds and the documented state path.
- Gitleaks 8.30.1 scanned 119 commits reachable from all local refs with no
  findings before publication. This is a scanner result, not a guarantee that
  every possible sensitive value is detectable. Verification evidence remains
  on the operator host in `runtime/doom-team-publication-20261022/`.
- [Publication CI](https://github.com/erkamspyjamas/doom-team/actions/runs/39831718290)
  **failed at lint in both jobs**: eight E721 exact-type comparisons and three
  E501 test line lengths. Type and test stages did not execute. These are
  existing source/test issues; publication changed documentation and ignores,
  not engine behavior. No fresh full-suite pass is claimed. The earlier pinned
  evidence below is retained with its original scope.
- The package license declaration remains Proprietary. Public visibility does
  not add an open-source license. No runtime restart or paid mission was launched.

## Runtime and publication

| Area | Verified state |
|---|---|
| Immutable release baseline | `v0.1.0-rc14`, `5f090e7fd52b73effee769175e4274a4217b4ad0`; trusted task/final Python quality profile, verified offline |
| Latest verified GitHub publication | Main `557af9986585420330992e737d6a791dd02258ec`; RC10–RC13 published and verified, RC13 `0a69725dbe355aa58e1acb75597a18d7a05f8992`; local RC14 publication pending |
| Local editable feature | Additional untagged `project` policy and technology-independent engineering route; console defaults to native-stack jobs. See evidence below. |
| RC9 complete editable suite | 750 passed; Ruff and strict mypy passed |
| Earlier RC9 affected installed suite | 53 affected approval/supervisor/mission-ledger tests passed |
| RC9 complete installed suite | 750 passed in a frozen RC9 snapshot; actual offline wheel build/install passed; see `BASE-01` |
| RC10 installed validation | 760 external installed tests and 760 fresh final installed tests passed; actual wheel builds/installs, Ruff and strict mypy passed |
| RC11 installed validation | 799 external installed tests and 799 fresh final installed tests passed; actual wheel builds/installs, Ruff and strict mypy passed |
| RC12 installed validation | 2272 external checks, 853 external installed tests and 853 fresh final installed tests passed; real wheel builds/installs, Ruff and strict mypy passed |
| RC13 installed validation | 863 tests passed after real offline wheel build/install; 42 targeted planner tests, Ruff and strict mypy passed; parser limits and policies unchanged |
| RC14 installed validation | 883 tests passed after real offline wheel build/install; 20 quality-policy regressions, 199 affected tests, actual Ruff/mypy positive/negative probes and source quality passed |
| Models | DeepSeek V4.1 Flash planner/author; independent GLM 5.3 Flash QA/Reviewer; high reasoning |

The installed CLI uses the current local checkout through its editable
installation. Publication state and the runtime baseline are listed separately
so local changes are distinguished from verified GitHub refs. Publication
records below describe their recorded remote snapshots; newer local releases
remain unpublished until separately authorized. Inspect them with `git log -1`,
`git show v0.1.0-rc14 --stat` and `git ls-remote origin`.

## Demonstrated behavior

- TECH-01 removes the operator console's inherited PocketLedger-only stack
  restriction. New jobs choose their languages/frameworks/dependencies and use
  native setup/build/check/test commands in `doom.project.json`, before QA,
  Reviewer and fresh final validation. Old mission policies/evidence are retained.
  Eighteen new-policy tests and 30 current console tests passed; real Node tests
  (positive/negative) traversed the mission lifecycle. A real React dependency
  installation/build/check/test passed from a fresh isolated wheel installation.
  Ruff and strict mypy passed (40 engine modules, six console modules).
  The broad regression run recorded 897 passes and one unchanged CLI shutdown
  test timing out at five seconds. All 15 tests in that module passed on isolated
  repeat; the initial failure remains recorded and is not treated as a clean
  full-suite pass. Source of the CLI/test matches immutable RC14 exactly.
  Evidence: `runtime/doom-console/verification/project-policy-verification.json`;
  the new policy is documented in `runbooks/PROJECT_VALIDATION.md`.

- QUAL-01 fixed the task/final validation-policy mismatch exposed by
  PocketLedger V2. The optional trusted `python-quality` profile executes
  compile, pytest, Ruff and strict mypy before task QA/Reviewer and on fresh
  final validation. Worker/mission fingerprints prevent changing quality
  commands/target on worker dispatch or resume. Default policy is unchanged.
  Twenty regressions, 199 affected tests and real-tool positive/negative
  proofs passed. An initial full run found two new settings missing from
  CONFIGURATION documentation (882 passed, one failed); after adding them,
  a fresh offline wheel build/install and all 883 tests passed. V2 was stopped
  by user authorization with zero integrations, known USD 0.02202305 plus
  possible unreported interrupted-call charge. Its original history remains
  unchanged. Same-contract V3 reached human review: 5/5 tasks in eight attempts,
  88 tests and 445 external checks passed. Candidate
  `7b0b54aa5ad3c760c91a4d03bed00c86bdb4e793`; known USD 0.12593979.

- PLAN-01 fixed the PocketLedger planning blocker's undisclosed size limits.
  RC13 renders the prompt from actual configured task/description/dependency
  limits and existing criterion/identifier constants. Parsing and limits
  remain unchanged: overlong criteria still fail closed. Two new prompt
  regressions failed before the fix; all 42 targeted tests and all 863
  installed product tests pass afterward, with real offline wheel build/install,
  Ruff and strict mypy. The PocketLedger V2 repeat passed planning but was later stopped on the
  separate quality-policy mismatch; see QUAL-01 and the retained V2 report.

- The medium greenfield PocketLedger showcase failed before mission creation.
  Its first task contained an acceptance criterion exceeding the enforced
  500-character bound; this bound was absent from the planner system profile.
  One provider call completed normally (known USD 0.00550958), but no task,
  candidate or external delivery check ran. This is an end-to-end failure,
  not a successful project or provider outage. Preserve it alongside the
  earlier successful bounded cases; see `PLAN-01` and `SHOW-01` in TODO.

- RES-01 completed a real SIGKILL/new-process resume on its third exercise.
  Both dependent fixture tasks integrated in three attempts; the completed
  task was unchanged, the interrupted attempt retained, retry budgets and
  routing preserved, and terminal resume did no work or provider calls.
  All 38 independent checks, 13 fixture tests, Ruff and strict mypy passed.
  V1's operator fixture lint failure and V2's observer initialization race
  remain preserved; this is not a first-run pass. V3 used seven successful
  calls, known USD 0.00251134; all three runs total known USD 0.00612803,
  excluding possible unreported interrupted-call charges. No product runtime
  changes, fixture approval/integration, push or deployment occurred.

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

- RC13 discloses planner bounds but still rejects invalid responses without
  silent truncation or added semantic retries. Model compliance must be
  measured by the fresh showcase (`SHOW-01`); prompt alignment alone does
  not prove the full project passes. The initial failed raw plan was not
  retained, so its exact criterion text/length remains unavailable.
- The active MissionRunner executes ready tasks serially. Capability routing
  does not imply concurrent execution; the parallel target is `PAR-01`.
- Context selection is a deterministic task-focused heuristic. Only complete
  files up to 50000 characters are included within a 60000-character total
  context bound. An existing omitted file cannot be replaced from guesses.
  Larger required edits may block; see `CTX-01` and
  [the context contract](runbooks/INSTALL.md#task-workspace-context).
- Candidate wheel/venv isolation prevents stale package imports. It is not an
  OS filesystem/process sandbox; containment work is tracked as `ISO-01`.
- RES-01 closes one predetermined process-cut/recovery exercise in a small
  synthetic repository. It does not establish recovery under every failure,
  a general reliability rate or broader production readiness. OS containment,
  large-file editing and actual concurrency remain open in [TODO.md](TODO.md);
  the architectural goals in [ROADMAP.md](ROADMAP.md) are not all complete.

## Evidence index

These are retained operator-host artifacts under `/home/eko/doom`; they are not
bundled into the product repository or wheel. Older reports describe their
state at the time. Later approval/publication records supersede those state
labels without rewriting the historical reports.

| Evidence | Operator-host location | Scope |
|---|---|---|
| QUAL-01 / RC14 fix | `pilots/doom-team-quality-policy-fix-20261019/REPORT.md` | Real four-stage task/final quality evidence, drift protection, preserved documentation gate failure and final 883 installed passes |
| PocketLedger V2 stop | `pilots/doom-team-showcase-pocketledger-v2-20261019/VERIFICATION.md` | Planned five tasks; first candidate passed compile/58 tests/QA but Reviewer lacked quality evidence; operator-authorized stop, old attempts preserved |
| PocketLedger V3 preregistration | `pilots/doom-team-showcase-pocketledger-v3-20261019/README.md` | Identical objective/acceptance, new trusted quality policy, fresh state/destination; result pending |
| PLAN-01 / RC13 fix | `pilots/doom-team-plan01-bounds-fix-20261019/REPORT.md` | Default/custom prompt bounds, unchanged parser limits, 42 targeted tests and 863 installed tests, offline build/install and quality passed |
| PocketLedger initial showcase | `pilots/doom-team-showcase-pocketledger-20261019/VERIFICATION.md` | Failed before mission creation: overlong planner criterion; original result/cost preserved, no delivery claim |
| RES-01 first exercise | `pilots/doom-team-res01-process-resume-20261019/VERIFICATION.md` | Actual process recovery passed; overall blocked by operator fixture/preflight error; retained unchanged |
| RES-01 second exercise | `pilots/doom-team-res01-process-resume-v2-20261019/VERIFICATION.md` | Corrected fixture gates passed; retained external observer initialization-race failure, not a demonstrated model plan failure |
| RES-01 successful third exercise | `pilots/doom-team-res01-process-resume-v3-20261019/VERIFICATION.md` | Actual SIGKILL/resume, unchanged completed task/budgets, terminal no-op and all final gates passed; prior failures retained |
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
