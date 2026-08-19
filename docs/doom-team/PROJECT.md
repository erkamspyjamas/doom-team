# Doom Team — Project Engineering Context

Status: ACTIVE DEVELOPMENT
OpenClaw Dev Team: v1 FEATURE FREEZE

## 1. Product

Doom Team is a local/self-hosted agentic software engineering
platform.

Doom Core is its deterministic orchestration operating system.

The target flow is:

Human objective
→ planning
→ dynamic role/capability routing
→ DEV implementation
→ repository evidence
→ QA
→ Reviewer
→ deterministic release gate
→ human approval boundary
→ optional push/merge/deploy

The objective is not merely to make agents execute commands.

The objective is to complete real business goals with low human
attention while preserving deterministic safety boundaries.


## 2. Main product vs helper

Doom Team / Doom Core is the product.

OpenClaw Dev Team is only the engineering helper used to develop Doom
Team.

OpenClaw Dev Team v1 is feature-frozen.

Do not add Plan Runtime, Goal Registry, controller layers, new
control-plane abstractions, or other OpenClaw features unless a real
Doom Team engineering task proves that they are required.


## 3. Authorized development repository

Primary repository:

/home/eko/doom-team/autopilot/doom-core

Branch:

openclaw/autopilot

Live Doom Core is NOT the normal development workspace:

/home/eko/doom-team/core

Live SQLite is NOT the normal development database:

/home/eko/doom-team/data/doom-v02.sqlite

Normal engineering tasks must not mutate live Core or live SQLite.


## 4. Core design principle

Give AI broad freedom inside reversible workspaces.

Apply strict deterministic guards only at expensive, irreversible,
security-sensitive, or shared-state boundaries.

The agent may choose how to:

- inspect the repository
- implement
- refactor
- create or remove project files
- debug
- write tests
- run tests/builds
- create a local commit

Do not micromanage implementation unless evidence shows that a
specific restriction is necessary.


## 5. Hard boundaries

Hard-gate:

- live deploy
- protected/shared branch push or merge
- live destructive DB mutation
- DB reset
- destructive migration
- secrets and credentials
- uncontrolled workspace escape
- evidence destruction
- acceptance bypass
- unlimited retry/replan/recovery
- proven semantic duplicate/infinite loops
- irreversible action under real requirement ambiguity

Do not use:

- git reset --hard
- git clean
- force checkout
- force push
- git add .


## 6. LLM responsibility

The LLM:

- understands the objective
- reasons about the implementation
- selects a solution
- writes/refactors code
- tests and debugs
- produces evidence
- proposes recovery when necessary

Doom Core should not attempt to reproduce all of the LLM's reasoning
with layers of narrow classifiers.


## 7. Deterministic Core responsibility

Doom Core deterministically enforces:

- permissions and grants
- durable task contracts
- workMode and mutation boundaries
- dynamic role/capability routing
- workspace/worktree isolation
- repository evidence
- acceptance gates
- recovery budgets
- Supervisor release decisions
- final human approval boundaries


## 8. Task semantics

Preserve the shared executable-action contract.

Important principles:

- mutation intent primarily comes from executable instruction
- expectedOutcome and acceptanceCriteria describe post-state/evidence
- validation deliverables are not automatically repository mutations
- inspected/reference inputs are not output mutation targets
- QA/Reviewer read-only work must not accidentally become modify work
- production routing must not depend on hard-coded mission/task/agent IDs
- dynamic roster, capabilities, tools, durable contract and DAG are
  preferred


## 9. Repair rather than reject

A valid business objective should not fail merely because a
probabilistic model produced an imperfect but recoverable representation.

Prefer deterministic repair / bounded recovery for:

- recoverable structured JSON
- missing or repairable schema fields
- safe role reassignment
- safe workMode normalization
- dependency ordering problems
- recoverable QA/Reviewer plan structure
- transient provider/runtime failures
- bounded continuation when real progress exists

Do not convert every representation error into a terminal mission
failure.


## 10. Provider/runtime failures

Distinguish platform-contract failures from runtime/provider failures.

Examples:

- HTTP 429
- transient provider errors
- malformed structured final output
- runtime timeout
- tool-loop/token limits

These do not automatically prove that the business objective is
invalid.

Use bounded recovery where safe.


## 11. Recovery

Recovery must remain:

- durable
- bounded
- deterministic
- evidence-aware
- fail-closed at real safety boundaries

Fail-closed does NOT mean every recoverable error should become a
terminal block.


## 12. Evidence and worktree safety

Repository/Git evidence is stronger than an agent's final prose.

Preserve:

- task workspace isolation
- commit provenance
- exact changed files
- integration/base separation
- untracked user evidence
- crash-window evidence

Known important risk:

Forced failed-task/replan cleanup can destroy dirty/untracked forensic
evidence before it is durably recorded.

Prefer lossless cleanup and explicit provenance.


## 13. Supervisor

Deterministic Supervisor decisions include:

- CONTINUE_WITH_BLOCKER
- READY_FOR_FINAL_APPROVAL
- LOOP_DETECTED
- HUMAN_REQUIRED

Critical rule:

PACKAGE ACCEPTED alone must never authorize the next sequential DEV
package.

Continuation requires a genuinely new, evidenced, non-stale blocker.

READY_FOR_FINAL_APPROVAL remains human-gated.


## 14. Engineering workflow

Normal helper workflow:

Human objective
→ DEV
→ local commit + tests + proof
→ workboard_complete
→ QA dependency becomes runnable
→ independent QA
→ workboard_complete or workboard_block
→ Reviewer
→ independent review
→ workboard_complete or workboard_block
→ human boundary

Lifecycle tools are direct model tools:

- workboard_heartbeat
- workboard_complete
- workboard_block

CLI/Gateway lifecycle workarounds are not the normal worker protocol.


## 15. DEV

DEV has broad reversible implementation freedom.

DEV may inspect, edit, refactor, test, debug and make a local commit.

DEV must not push, merge, deploy, mutate live Core/DB, expose secrets,
or destroy existing evidence.


## 16. QA

QA is independent and normally repository-read-only.

QA verifies:

- objective
- implementation diff
- regression coverage
- actual tests
- architecture/invariants
- evidence

FAIL or insufficient evidence must block rather than silently pass.


## 17. Reviewer

Reviewer is independent and read-only.

Reviewer evaluates:

- whether the implementation actually solves the objective
- regression risk
- architecture fit
- QA evidence
- safety boundaries
- remaining risk

Reviewer approves or produces concrete remediation.


## 18. Production-readiness priorities

P0 — Mission Completion Reliability

Reduce false terminal blocks caused by recoverable LLM/runtime
variation.

Priority areas:

- structured output resilience
- partial/malformed JSON recovery
- shared task semantics
- transient provider/runtime error taxonomy
- bounded continuation
- zero-task planning recovery
- unnecessary replan reduction
- false-block reduction

P1 — Worktree / Forensic Safety

- lossless failed-task cleanup
- dirty/untracked evidence preservation
- crash-window provenance
- forensic snapshot before destructive cleanup

P1 — End-to-End Mission Acceptance

Repeatedly prove:

objective
→ implementation
→ QA
→ Reviewer
→ final approval boundary

P1 — Observability

Measure and expose:

- human attention
- manual reclaims
- manual moves
- false blocks
- duplicate cards
- stuck runs
- wrong mutations
- successful objectives
- recovery causes
- provider/runtime vs platform failures

P2 — Advanced automation

Only after reliability evidence justifies it.


## 19. Historical `ab` incident

A historical v0.12.19 mission showed a planning failure class involving:

STRUCTURED_FINAL_INVALID_JSON_AFTER_RECOVERY

and a blocked + zero-task state.

Treat this only as historical evidence of a reusable failure class.

Do not:

- assume the historical mission is still in the same state
- mutate live DB to reproduce it
- hard-code its mission ID
- build a fixture-specific repair

Current repository evidence is authoritative.


## 20. Source priority

Use evidence in this order:

1. Current authorized repository source and Git state
2. Tests actually executed
3. Runtime/Workboard evidence when relevant
4. Current project documentation
5. Historical snapshots/prompts
6. Assumptions

If documentation conflicts with source + tests, source + tests win.


## 21. Completion standard

Do not claim success without evidence.

Minimum engineering closeout:

PROBLEM / ROOT CAUSE
CHANGED FILES
IMPLEMENTATION
REGRESSION TESTS
COMMANDS ACTUALLY RUN
TEST RESULTS
GIT COMMIT
QA RESULT
REVIEWER RESULT
REMAINING RISKS


## 22. Current operating decision

OpenClaw Dev Team v1:
FEATURE FREEZE

Doom Team / Doom Core:
ACTIVE DEVELOPMENT

Push:
NOT APPROVED

Merge:
NOT APPROVED

Deploy:
NOT APPROVED

Live Core / DB mutation:
NOT APPROVED

New engineering work should produce Doom Team product value.
