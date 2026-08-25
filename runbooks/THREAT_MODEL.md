# Threat Model

## Assets

- Candidate evidence and verdicts
- Workspace state and preserved artifacts
- Human approval decisions

## Threats

- Stale evidence used to accept a newer candidate: mitigated by candidate-bound evidence and freshness checks.
- QA/Reviewer mutation: validators receive only candidate and evidence, no workspace/repository access.
- Workspace escape via symlinks or path traversal: `escape_check` rejects any symlink resolving outside the workspace; path resolution enforces workspace scope.
- Unbounded duplicate work: `RecoveryController` detects duplicate evidence digests and consumes semantic budget.
- Unauthorized release: `ApprovalBoundary` gates irreversible actions; no push/merge/deploy is embedded in the autonomous path.
- Loss of dirty/failed work: `preserve` retains failed/dirty workspaces before cleanup.

## Mitigations

- All evidence is digest-bound and persisted durably.
- QA/Reviewer are read-only by construction.
- Workspace isolation and escape detection are enforced by `GitWorkspace`.
- Semantic recovery is bounded and restart-safe.
- Human approval is required for any irreversible operation.
