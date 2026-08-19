# Production Readiness Phase 1F — Bounded Failure Recovery

## Objective

Find and fix exactly one NEW, genuinely reproducible, high-impact Doom Core reliability defect involving recovery from a real mission failure.

Prefer recoverable errors permanently stranding missions, retry losing valid progress, recovery duplicating work, unbounded retry, transient failures becoming terminal, or terminal failures retrying forever.

Do not invent a defect. If no qualifying defect exists, BLOCK with evidence.

## DEV requirements

Reproduce the defect, prove root cause, implement deterministic bounded recovery, add positive and negative/safety regression coverage, run targeted and full safe tests, and run `git diff --check`.

Leave exactly ONE local DEV commit after baseline.

Recovery must have explicit bounds.

## Safety

No push, merge, deploy, live Core/DB mutation, secrets, destructive git/migrations, or control-plane development.

Never use `git clean`, `git reset --hard`, or `git add .`.

## Lifecycle contract

`DEV_RESULT=PASS:`

`QA_VERDICT=PASS:`

`REVIEWER_VERDICT=APPROVE:`

Every successful role must attach passed proof.
