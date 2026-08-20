# Production Readiness Phase 2B — Forensic Retention Artifact

## Objective

When cleanup is required after a failed task/recovery/replan, preserve
otherwise losable worktree evidence before cleanup.

Use a bounded, deterministic and reusable retention mechanism appropriate
to the existing architecture, such as retained workspace, patch, bundle,
or equivalent artifact.

The retained evidence must be durably bound to the relevant mission/task
provenance.

Do not invent work if Phase 2A evidence shows the contract is already
satisfied.

## DEV requirements

- reproduce the evidence-loss window
- prove root cause
- retain the minimum sufficient forensic artifact
- bind retention provenance to the affected task/recovery operation
- cover success and refusal/failure cases
- preserve user/untracked evidence
- targeted + full safe tests
- typecheck/build where applicable
- `git diff --check`
- exactly ONE local DEV commit after baseline

## Safety

No push, merge, deploy, live DB mutation, destructive Git, secrets,
`git clean`, `git reset --hard`, or `git add .`.

## Lifecycle contract

`DEV_RESULT=PASS:`

`QA_VERDICT=PASS:`

`REVIEWER_VERDICT=APPROVE:`

Every successful role must attach passed proof.
