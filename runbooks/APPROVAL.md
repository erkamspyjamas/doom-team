# Approval Runbook

## Boundary

Irreversible or shared-state actions (push, protected merge, deploy, destructive DB) require explicit human approval. The autonomous path never performs them without a positive approval from `ApprovalPort`.

## Procedure

1. A candidate passes QA, Reviewer, and objective checks.
2. The deterministic `Supervisor.decide` is called with the candidate, fresh evidence, and no approval port. It returns `READY_FOR_FINAL_APPROVAL`.
3. A human reviews the evidence and approves via the `ApprovalPort` (e.g., manual confirmation).
4. Only after a positive approval does the Supervisor return `RELEASE_AUTHORIZED`.

No release action (push/merge/deploy) may be taken without `RELEASE_AUTHORIZED`.
