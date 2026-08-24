# Doom Team — Acceptance Contract

Mandatory invariants:

- stale evidence can never accept a newer candidate
- QA cannot implement changes
- Reviewer cannot implement changes
- workspace escape is rejected
- failed dirty workspaces are preserved
- runtime retries cannot reopen exhausted semantic recovery
- duplicate/no-progress remediation becomes bounded
- generic orchestration completion cannot bypass Doom acceptance
- actual provider/model/runtime is observable
- protected/shared irreversible operations remain human-gated

Mandatory system scenarios:

1. One objective completes DEV → QA → Reviewer → Supervisor.
2. Reviewer blocker creates a fresh remediation attempt/candidate.
3. QA blocker creates a fresh remediation attempt/candidate.
4. Old proof is rejected for a new candidate.
5. Legitimate no-code-change remediation still requires fresh evidence.
6. Provider/session transient failure does not consume semantic budget.
7. Semantic exhaustion survives restart.
8. QA mutation is rejected/detected.
9. Reviewer mutation is rejected/detected.
10. Failed dirty/untracked workspace is retained.
11. Three independent tasks execute in isolated state.
12. DAG blocks premature downstream work.
13. Duplicate semantic work is bounded.
14. Unauthorized push/merge/deploy is denied.
15. A representative 20+ task mission reaches terminal accounting with zero unexplained stuck work, unsafe mutation, stale-proof acceptance and unbounded duplicate growth.
