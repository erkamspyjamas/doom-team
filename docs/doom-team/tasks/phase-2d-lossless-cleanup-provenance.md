# Production Readiness Phase 2D — Lossless Cleanup Provenance

## Objective

Make cleanup results explicit, deterministic and auditable.

Cleanup/recovery must end with explicit provenance equivalent to:

- cleaned
- retained
- refused
- human_required

A cleanup path must never silently discard evidence or report success
without durable state/provenance supporting that claim.

## DEV requirements

Find one real remaining provenance gap after Phase 2A-2C, reproduce it,
implement a bounded reusable fix, add positive and negative/safety
regression coverage, run targeted/full safe validation and
`git diff --check`, and leave exactly one local DEV commit after
baseline.

## Safety

No push, merge, deploy, live DB mutation, destructive Git, secrets,
`git clean`, `git reset --hard`, or `git add .`.

## Lifecycle contract

`DEV_RESULT=PASS:`

`QA_VERDICT=PASS:`

`REVIEWER_VERDICT=APPROVE:`

Every successful role must attach passed proof.
