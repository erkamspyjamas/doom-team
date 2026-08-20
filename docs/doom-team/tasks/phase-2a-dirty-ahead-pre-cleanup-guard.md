# Production Readiness Phase 2A — Dirty/Ahead Pre-Cleanup Guard

## Objective

Implement the first Worktree / Forensic Safety slice.

Find and close a genuinely reproducible Doom Core product reliability
gap in which failed-task recovery, replan, retry, or cleanup can destroy
or discard unpreserved worktree evidence.

The required primary behavior is:

dirty or ahead worktree
+
evidence not durably preserved
->
cleanup must be refused fail-closed.

Do not invent a defect. If the existing implementation already fully
satisfies this contract, BLOCK with evidence rather than creating
semantic duplicate work.

## DEV requirements

1. Reproduce the unsafe cleanup path or demonstrate the precise missing
   guard.

2. Identify root cause from repository code and tests.

3. Implement one reusable deterministic pre-cleanup classification /
   guard.

4. At minimum distinguish relevant states such as:
   - clean
   - dirty
   - branch/HEAD ahead or containing unintegrated work
   - unsafe / uncertain state

5. Destructive or evidence-losing cleanup must not proceed when
   unpreserved dirty/ahead evidence exists.

6. Preserve legitimate cleanup/recovery for demonstrably safe state.

7. Add positive regression coverage.

8. Add negative/safety regression coverage proving dirty/ahead evidence
   cannot silently disappear.

9. Run targeted tests, full safe suite, typecheck/build where applicable,
   and `git diff --check`.

10. Leave exactly ONE local DEV commit after the phase baseline.

## Safety

No push, merge, deploy, live Doom Core/DB mutation, secret access,
destructive migration, force push, `git clean`, `git reset --hard`,
or `git add .`.

Existing untracked/forensic evidence must be preserved.

Do not develop the OpenClaw control plane unless a product test proves
that Phase 2A cannot be implemented without a concrete control-plane
dependency.

## Lifecycle contract

Successful DEV summary starts:

`DEV_RESULT=PASS:`

Successful QA summary starts:

`QA_VERDICT=PASS:`

Successful Reviewer summary starts:

`REVIEWER_VERDICT=APPROVE:`

Every successful role must attach passed proof.
