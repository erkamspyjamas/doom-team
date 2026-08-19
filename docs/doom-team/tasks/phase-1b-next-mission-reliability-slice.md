# Production Readiness Phase 1B — Next Mission Reliability Slice

## Goal

Find and fix one NEW, genuinely reproducible, high-impact mission-completion
reliability failure in the current Doom Core implementation.

This phase must be distinct from Phase 1A's excess-closing-brace JSON repair.

## Selection

DEV chooses the slice from CURRENT source and tests.

Prefer a failure that can incorrectly:

- block a legitimate mission,
- lose mission progress,
- corrupt mission state,
- duplicate mission execution,
- prematurely terminate a mission,
- or report completion incorrectly.

Do not choose a historical-fixture-only problem.

## Engineering requirements

Before completion DEV must:

1. deterministically reproduce the failure;
2. demonstrate the root cause in the current code path;
3. implement a reusable bounded repair;
4. add positive regression coverage;
5. add negative/safety regression coverage;
6. run relevant targeted tests;
7. run the full safe test suite;
8. run git diff --check;
9. leave exactly one local DEV commit after the injected baseline.

DEV has broad implementation freedom inside the authorized Doom Core repository.

Existing dirty/untracked evidence must never be destroyed.

## Forbidden

No:

- git clean
- git reset --hard
- git add .
- push
- merge
- deploy
- live Core mutation
- live DB mutation
- secret access
- OpenClaw/control-plane feature development

## Lifecycle

DEV succeeds only with:

DEV_RESULT=PASS

Independent QA must validate the implementation and evidence.

QA succeeds only with:

QA_VERDICT=PASS

Independent Reviewer performs final architecture/safety review.

Reviewer succeeds only with:

REVIEWER_VERDICT=APPROVE

Any blocker, failed validation, ambiguous evidence, or remediation requirement
must stop automatic phase advancement.
