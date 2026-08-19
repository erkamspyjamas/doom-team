# Production Readiness Phase 1C — Mission State Integrity & Resume

## Objective

Find and fix exactly one NEW, genuinely reproducible, high-impact Doom Core reliability defect involving mission state integrity, persistence, restoration, resume, or continuation after interruption.

The defect must be distinct from Phase 1A and Phase 1B JSON repair work.

Prefer failures where legitimate mission progress is lost, restored incorrectly, corrupted by partial state, stranded after restart/recovery, or overwritten by stale state.

Do not invent a defect. If no qualifying reproducible defect exists, BLOCK with evidence.

## DEV requirements

- Reproduce the defect.
- Prove the root cause.
- Implement a reusable bounded fix.
- Add positive regression coverage.
- Add negative/safety regression coverage.
- Run targeted tests.
- Run the full safe test suite.
- Run `git diff --check`.
- Leave exactly ONE local DEV commit after the phase baseline.
- Leave the tracked worktree clean.

## Safety

No push, merge, deploy, live Doom Core/DB mutation, secrets, destructive migrations, control-plane feature development, `git clean`, `git reset --hard`, or `git add .`.

## Lifecycle contract

DEV success summary MUST begin exactly:

`DEV_RESULT=PASS:`

QA success summary MUST begin exactly:

`QA_VERDICT=PASS:`

Reviewer approval summary MUST begin exactly:

`REVIEWER_VERDICT=APPROVE:`

Every successful role must attach at least one `passed` proof.
