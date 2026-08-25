# ADR-0007: Operability Contract

## Status

Accepted

## Context

Doom Team will run as a durable service. Operators need a precise contract for process lifecycle, state ownership, configuration, approval boundary, restart/idempotency, health/readiness, and irreversible actions. This contract must be testable so that later Phase 2 implementations comply.

## Decision

We define the following operability contract for any Doom Team service implementation:

1. **Process lifecycle**: The service starts by initializing all adapters (workspace, repository, evidence store, verdict store, recovery controller, telemetry, orchestration adapter) from their durable state directories. Startup must be idempotent and must never destroy existing state. Shutdown must be graceful and must preserve all failed/dirty/uncertain workspaces and pending evidence.

2. **Durable state ownership**: The service owns the state directories it is configured with. It must create them if missing, must not write outside them, and must support backup/recovery per the runbooks. Durable state includes: evidence records, verdict records, recovery state, orchestration run mappings, and preserved workspaces.

3. **Configuration ownership and precedence**: Configuration is owned by operators. Precedence: explicit runtime overrides (CLI/env) > config file > defaults. Secrets must never be embedded in config files, logs, or evidence. Actual provider/model/runtime must be observable via telemetry.

4. **Human approval boundary**: All irreversible actions (push, merge, deploy, destructive DB, credential provisioning) require a positive human approval at runtime through `ApprovalPort`. The autonomous path never performs them. The `Supervisor` must not return `RELEASE_AUTHORIZED` without a positive approval.

5. **Restart semantics and idempotency**: The following must survive restart: semantic retry exhaustion, orchestration run mappings, evidence and verdict records, and preserved workspaces. The following must never double-apply: a candidate commit, a semantic retry consumption, a release. Runtime retries never consume the semantic budget.

6. **Health and readiness**: `healthy` means the process is alive and can serve basic operations. `ready` means all durable state is loaded and the service can accept and execute work. These must be reported distinctly (e.g., `/healthz` vs `/readyz`) and must never be conflated with acceptance (e.g., a run being `completed` does not imply readiness or acceptance).

7. **Irreversible actions**: The service must never autonomously perform: push, merge, deploy, destructive DB, or credential provisioning. These are human-only operations and require explicit operator approval.

## Consequences

- Later Phase 2 tasks implement against this contract.
- The contract is pinned by executable tests in `tests/test_operability.py`.
- Any implementation must keep all contract tests green and preserve the invariants above.
