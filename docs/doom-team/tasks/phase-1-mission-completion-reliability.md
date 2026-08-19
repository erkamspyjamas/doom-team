# Production Readiness Phase 1 — Mission Completion Reliability

## Objective

Continue making Doom Team production-ready.

Reduce cases where a valid and implementable business objective becomes
terminally blocked because of recoverable LLM/runtime variation.


## First task

Inspect the CURRENT repository implementation and tests.

Do not treat historical incidents as source-of-truth.

Find one genuinely reproducible, high-impact mission-completion failure
mode and repair it in a reusable way.


## Priority candidate classes

Evaluate these as candidates:

1. recoverable malformed/partial structured planning output
2. unnecessary terminal block after structured-final JSON repair failure
3. transient provider/runtime failure being treated like a planning
   contract failure
4. zero-task planning recovery gaps
5. false blocks caused by repeated semantic interpretation across
   redundant classifier/gate layers

You do NOT need to solve all of them in one task.

Choose the highest-impact slice that is both reproducible and safely
bounded by current repository evidence.


## Design requirement

Prefer improving a shared contract or reusable structural policy.

Avoid:

- one-fixture regex patches
- historical mission ID special cases
- agent/task ID hard-coding
- another redundant classifier unless evidence proves it is necessary


## Freedom

Inside the authorized repository you may choose the implementation
approach.

You may:

- inspect
- edit
- refactor
- create/remove project files
- add tests
- run tests/builds
- debug
- make exactly one local DEV commit

Do not stop merely because the first implementation approach fails.

Use evidence and try another safe approach.


## Safety boundaries

Do not:

- mutate /home/eko/doom-team/core
- mutate/reset /home/eko/doom-team/data/doom-v02.sqlite
- push
- merge
- deploy
- use git reset --hard
- use git clean
- force checkout/push
- use git add .
- read/expose secrets
- destroy existing untracked evidence
- develop OpenClaw/control-plane features


## Acceptance criteria

The task succeeds only if:

- the selected failure mode is reproduced from current source
- root cause is demonstrated
- fix is reusable
- no historical mission/task/agent ID is hard-coded
- positive regression coverage is added
- negative/safety regression coverage is added
- targeted tests pass
- full test suite passes
- git diff --check passes
- only relevant project files change
- exactly one local DEV commit is produced
- no push/merge/deploy occurs
- no live Core/DB mutation occurs
- QA independently passes or blocks with real evidence
- Reviewer independently approves or produces concrete remediation


## Closeout format

PROBLEM / ROOT CAUSE

WHY THIS WAS THE HIGHEST-IMPACT SLICE

CHANGED FILES

IMPLEMENTATION

REGRESSION TESTS

COMMANDS ACTUALLY RUN

TEST RESULTS

GIT COMMIT

QA RESULT

REVIEWER RESULT

REMAINING RISKS
