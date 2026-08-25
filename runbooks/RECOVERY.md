# Recovery Runbook

## Runtime Failures

Provider timeouts, HTTP 429/503, and session crashes are transient. They never consume the semantic retry budget.

- To recover a runtime failure, call `recover` on the orchestration adapter (e.g., `GasCityAdapter.recover(run_id)`).
- The adapter persists the mapping between Doom run ids and Gas City run ids in a durable store, so recovery survives restarts.

## Semantic Failures

QA/Reviewer blockers, repeated acceptance failures, no-progress and duplicate evidence are semantic failures. They consume the bounded semantic retry budget.

- Use `RecoveryController` to track per-task retry counts.
- `consume_semantic_retry` decrements the budget; when exhausted, `is_exhausted` returns True and persists across restarts.
- `next_attempt` returns None once exhausted, preventing further attempts.

## Preserving Dirty Workspaces

Failed or dirty workspaces are preserved for forensic analysis:

- Call `GitWorkspace.preserve(workspace_id)` to move the workspace to `preserved/` under the base directory.
- `destroy` will not delete preserved workspaces.
