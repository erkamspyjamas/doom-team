# Production Readiness Phase 1D — Duplicate Execution & Idempotency

## Objective

Find and fix exactly one NEW, genuinely reproducible, high-impact Doom Core reliability defect involving duplicate mission work, replay, retry, resume, or idempotency.

Prefer failures where successful work executes twice, retry repeats a side effect, resume replays completed work, duplicate events diverge mission state, or idempotency suppresses legitimate new work.

Do not invent a defect. If no qualifying defect exists, BLOCK with evidence.

## DEV requirements

Reproduce, prove root cause, implement a reusable bounded fix, add positive and negative regression coverage, run targeted and full safe tests, run `git diff --check`, and leave exactly ONE local DEV commit after baseline.

Negative coverage must show legitimate retries/new work remain possible.

## Safety

No push, merge, deploy, live Core/DB mutation, secrets, destructive git/migrations, or control-plane development.

Never use `git clean`, `git reset --hard`, or `git add .`.

## Lifecycle contract

`DEV_RESULT=PASS:`

`QA_VERDICT=PASS:`

`REVIEWER_VERDICT=APPROVE:`

Every successful role must attach passed proof.
