# Production Readiness Phase 1E — Completion Truth

## Objective

Find and fix exactly one NEW, genuinely reproducible, high-impact Doom Core defect where mission completion, success, or terminal state becomes incorrect.

Prefer premature completion, blocker/error interpreted as success, completion without corresponding state/evidence, premature terminal transition, or legitimate completion incorrectly rejected.

Do not invent a defect. If no qualifying defect exists, BLOCK with evidence.

## DEV requirements

Reproduce, prove root cause, implement a reusable bounded fix, add positive and negative/safety regression coverage, run targeted tests, full safe tests and `git diff --check`.

Leave exactly ONE local DEV commit after baseline.

Completion must remain fail-closed when evidence is ambiguous.

## Safety

No push, merge, deploy, live Core/DB mutation, secrets, destructive git/migrations, or control-plane development.

Never use `git clean`, `git reset --hard`, or `git add .`.

## Lifecycle contract

`DEV_RESULT=PASS:`

`QA_VERDICT=PASS:`

`REVIEWER_VERDICT=APPROVE:`

Every successful role must attach passed proof.
