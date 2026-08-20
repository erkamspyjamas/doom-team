# Production Readiness Phase 2C — Crash-Window & Replan Snapshot

## Objective

Protect task/worktree forensic truth across crash windows and replans.

Before a replan or cleanup transition can invalidate the previous working
state, retain enough durable evidence to reconstruct what existed.

Relevant evidence includes, where applicable:

- task state
- execution binding
- branch / HEAD
- changed files
- untracked evidence
- worktree relationship
- affected child/dependency state

## DEV requirements

Prove one real missing crash/replan preservation path, implement a
bounded reusable fix, add positive and negative regression coverage,
run targeted and full safe validation, and leave exactly one local DEV
commit after baseline.

## Safety

No push, merge, deploy, live DB mutation, destructive Git, secrets,
`git clean`, `git reset --hard`, or `git add .`.

## Lifecycle contract

`DEV_RESULT=PASS:`

`QA_VERDICT=PASS:`

`REVIEWER_VERDICT=APPROVE:`

Every successful role must attach passed proof.
