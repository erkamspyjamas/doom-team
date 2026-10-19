# Doom Team — Prioritized Work

Last reviewed: 2026-10-19. Current evidence and limitations are in
[STATUS.md](STATUS.md). The broad architectural phases remain in
[ROADMAP.md](ROADMAP.md). Stable IDs below are used in operation reports.

An unchecked item is planned work, not authorization to execute it. Each
operation has its own bounded human authorization and report. Paid runs need
a fixed objective, independent acceptance and runtime/cost bounds before
starting. Preserve failures and do not assist a generated candidate mid-run.

## P0 — Finish the current baseline

- [x] **PLAN-01 — Disclose enforced planner bounds.** Fixed as local RC13
  `0a69725dbe355aa58e1acb75597a18d7a05f8992`: generate system guidance from
  actual PlannerConfig plus existing criterion/identifier constants. Parser
  methods, numerical limits, providers, retries and approval policy unchanged.
  Before-fix prompt regressions reproduced; all 42 planner tests pass, including
  custom configs and 500/501-character rejection boundary. Real offline wheel
  build/install and the full 863-test installed suite, Ruff and strict mypy
  passed. Evidence: `pilots/doom-team-plan01-bounds-fix-20261019/REPORT.md`.

- [x] **BASE-01 — Complete RC9 installed baseline.** Run all 750 tests through
  the offline installed-candidate validator against immutable RC9, including
  real wheel build/install. Done when every stage passes, all snapshot inputs
  and runtime/test bytes match, and evidence is retained. No new paid pilot
  uses this baseline until the full gate passes. Earlier targeted 53-test
  installed evidence is not the full-suite result.
- [x] **DOC-01 — Maintain current status and work list.** Add STATUS/TODO,
  correct the README's obsolete documentation-only/Autopilot description,
  link them from README/ROADMAP and check local documentation links. Done
  when the documents distinguish runtime/publication state, tested evidence,
  known limits and remaining goals; commit only documentation changes.
- [x] **REL-01 — Publish the RC9 fix and documentation.** Requires explicit
  push authorization. Done when a normal fast-forward main push and the
  unchanged RC9 tag are verified against remote refs. Never force-update old
  tags. Deployment is a separate decision.

Completion evidence for BASE-01 and DOC-01 is retained on the operator host at
`/home/eko/doom/pilots/doom-team-rc9-installed-baseline-20261019/`:
750 installed tests passed after offline build/install, the frozen snapshot
and product runtime/test hashes matched, and 13 local documentation links
passed validation. The documentation-only commit is recorded in this file's
Git history. The immutable tested runtime tag remains RC9.

REL-01 publication evidence is retained at
`/home/eko/doom/pilots/doom-team-rc9-push-20261019/`: main and the immutable RC9
tag were pushed atomically and independently verified against GitHub refs.
This publication-status follow-up is documentation-only; its exact commit and
remote verification are recorded in that operation report. No deployment or
new paid pilot was started.

- [ ] **REL-03 — Publish verified RC10/RC11/RC12/RC13 and follow-up status.** Requires a
  separate explicit push authorization. Verify a normal main push and the
  unchanged RC10/RC11/RC12/RC13 tags against remote refs; do not deploy or move old tags.

## P1 — Broaden real-work evidence

- [ ] **SHOW-01 — Complete the medium PocketLedger project showcase.**
  Initial run failed at planning (one call, known USD 0.00550958, no mission
  or implementation). After PLAN-01 validation and fresh run authorization,
  retain the exact project objective and external acceptance from
  `pilots/doom-team-showcase-pocketledger-20261019/`; use a fresh destination
  and state. Done only at the normal human-review boundary with all internal
  and independent HTTP/persistence/CSV/static-contract/Ruff/strict-mypy/pytest
  gates passed. Preserve initial failure and costs; no easier replacement,
  in-run assistance or general perfect-system claim. Browser click/visual
  testing is not covered by the frozen external harness.
  User authorized the fix and repeat; fresh V2 is preregistered at
  `pilots/doom-team-showcase-pocketledger-v2-20261019/`, with byte-identical
  objective/acceptance and unchanged selected high-reasoning model profile.
  Leave SHOW-01 open until its own complete outcome is verified.

The following three cases use fresh destinations/state and new external
acceptance criteria. They are separate from the frozen benchmark. Retain
all outcomes, retries, blockers, elapsed time and known/unknown costs; record
operator code interventions explicitly. Do not infer a success rate from
only the cases that passed.

- [x] **PROOF-01 — Bug fix in an existing repository.** Pre-register a real
  defect, regression and independent acceptance. Done when the mission
  naturally reaches human review and acceptance passes on its exact SHA.

PROOF-01 evidence: `pilots/doom-team-proof01-active-retry-20261019/VERIFICATION.md`;
55 independent checks, 760 external and 760 fresh final installed tests passed.
The exact candidate was explicitly approved and integrated as local RC10; see
`pilots/doom-team-proof01-release-rc10-20261019/REPORT.md`.

- [x] **PROOF-02 — Feature in an existing module/API.** Require preservation
  of existing behavior and explicit API tests. Use the same independent
  acceptance and candidate-identity standard as PROOF-01.

PROOF-02 evidence: `pilots/doom-team-proof02-history-query-20261019/VERIFICATION.md`;
107 independent checks, 799 external and 799 fresh final installed tests passed.
Both tasks passed first attempts. The exact candidate was approved/integrated
as local RC11; see `pilots/doom-team-proof02-release-rc11-20261019/REPORT.md`.

- [x] **PROOF-03 — Refactor with preserved behavior.** Fix the external
  behavior contract before execution. Done when old behavior and the
  required refactor are independently verified on the candidate.

PROOF-03 evidence: the initial refactor remains review-blocked in
`pilots/doom-team-proof03-redaction-refactor-20261019/VERIFICATION.md`. A fresh
autonomous follow-up in `pilots/doom-team-proof03-redaction-fix-20261019/`
passed 2272 independent checks and both complete 853-test installed suites.
Only the final composite was approved/integrated as RC12. Retain both runs and
their known USD 0.02821515 total; do not count this as a first-pass success.

- [x] **RES-01 — Actual interruption/resume exercise.** Interrupt a bounded
  real process at a pre-registered point, then resume from durable state.
  Verify preserved plan/retry budgets and provenance, no duplicate task
  integration, and no unreviewed frontier advancement.

RES-01 completion evidence:
`pilots/doom-team-res01-process-resume-v3-20261019/VERIFICATION.md` and
`verification/summary.json`. Actual SIGKILL/new-process resume preserved the
completed task, plan, routing and retry budgets; exactly two reviewed frontier
advances and a provider-free terminal no-op. All 38 independent checks,
13 fixture tests, Ruff and strict mypy passed on exact candidate
`321ff68bfc573121e4584add0d79c55a78385257`. Two tasks, three attempts,
seven successful calls; known USD 0.00251134 for V3. Keep V1's operator fixture
lint failure and V2's external observer initialization race as failed outcomes,
not model failures erased by this success. All three known costs total
USD 0.00612803, excluding possible unreported interrupted-call charges.
The synthetic candidate remains unapproved; no product runtime change,
integration, push or deployment was needed to complete this exercise.

- [ ] **ISO-01 — OS-level validation containment.** Define filesystem,
  process and network boundaries for model-authored build/test code. Verify
  those boundaries using deterministic probes before claiming containment.
  The existing sanitized environment and import-isolated venv remain useful
  but are not the completion evidence for this item.

## P2 — Scale and harden execution

- [ ] **CTX-01 — Large-file/repository editing.** Add bounded explicit file
  retrieval and/or targeted edits for required source above current limits.
  Verify that omitted content is not reconstructed and that unrelated code
  survives edits; retain independent QA and candidate-bound validation.
- [ ] **PAR-01 — Concurrent independent DAG tasks.** Implement bounded
  concurrency, fan-out/fan-in, conflict handling and reviewed integration
  provenance. Demonstrate at least three independent concurrent tasks and
  dependent work afterward; preserve the validated serial path.
- [ ] **CLOCK-01 — Investigate clock-sensitive test ordering.** Reproduce
  the preserved RC6 failure in which wall-clock timestamps went backward.
  Make test timing deterministic where appropriate while retaining UTC
  audit timestamps and existing approval/validation gates. Do not rewrite
  the historical result or assume its root cause without evidence.
- [ ] **REL-02 — Fresh release regression evaluation.** After material
  execution changes, authorize a new bounded run against the unchanged
  frozen benchmark and compare with historical profiles. Preserve the
  35-file manifest, old commits/tags and explicitly fixed runtime policy.
- [ ] **PROD-01 — Evidence-based production readiness.** Define measurable
  reliability, recovery, containment, operating and release criteria; link
  each criterion to evidence. Phase headings or a single successful pilot
  do not close this item. Deployment remains separately authorized.

## Updating this list

1. Change a checkbox only when its completion criteria have evidence.
2. Record the operation path/commit and whether results are historical or
   current; update STATUS's runtime/publication rows when they change.
3. Keep failed attempts and old reports. A later fix does not turn an old
   blocked mission into a pass.
4. When code changes, review which quality checks and baseline runs must be
   repeated. Documentation-only updates do not redefine a frozen source tag.
5. Stop at the end of each authorized operation; do not automatically push,
   approve a mission, deploy or start another paid case.
