# Threat Model

This document describes the **current implemented** security posture of Doom Team. It intentionally avoids overstating protections that are not present.

## Assets

- Candidate evidence and verdicts
- Workspace state and preserved artifacts
- Human approval decisions

## Execution Environment

Model-authored code is **not** executed inside a true OS/container sandbox. Validation commands and tests may run with the privileges and environment available to the Autopilot worker, including network access and any inherited provider credentials. Workspace isolation is a logical/process boundary (per-task Git workspaces under a managed base directory), not a complete security sandbox.

## Threats

- **Stale evidence used to accept a newer candidate** — mitigated by candidate-bound evidence and freshness checks (`evidence_is_fresh`).
- **QA/Reviewer mutation** — validators receive only the candidate and its evidence; no workspace or repository handle is passed to them.
- **Workspace escape** — `GitWorkspace.escape_check` is a **post-hoc detection** mechanism, not enforcement. It checks for symlinks that resolve outside the workspace directory after execution has occurred; it cannot prevent arbitrary execution or side effects that already happened. See Residual Risks.
- **Unbounded duplicate work** — `RecoveryController` detects duplicate evidence digests and consumes the bounded semantic budget.
- **Unauthorized release** — the `Supervisor` cannot return `RELEASE_AUTHORIZED` without a positive human `ApprovalPort` decision; no push/merge/deploy is embedded in the autonomous path.
- **Loss of dirty/failed work** — `preserve` retains failed/dirty workspaces before cleanup.

## Compensating Controls

The actual mitigation story relies on the following controls:

- **Isolated per-task workspaces** — each task runs in its own Git-backed directory under a managed base; cross-task contamination is prevented by separation, not by OS sandboxing.
- **Mutation/path boundaries and sensitive-path rejection** — the permission engine restricts `EDIT` to paths within the workspace root and denies dangerous capabilities (`PUSH`, `MERGE`, `DEPLOY`, `DESTRUCTIVE_DB`) unless a human approval is recorded.
- **Command allowlisting (where applicable)** — repository-level operations in `GitWorkspace` are limited to a small set of Git subcommands (e.g., `git apply`); the mission runner accepts an arbitrary `TaskRunner` callable and is therefore not a complete command allowlist.
- **Durable evidence/provenance with candidate binding** — evidence is persisted and bound to a specific candidate (id and commit); freshness checks reject stale evidence.
- **Deterministic validation gates** — the `Supervisor` accepts only candidates with fresh evidence, passing QA, passing Reviewer, and a passing objective check.
- **Independent QA and Reviewer verification** — QA and Reviewer are read-only by construction and operate only on candidate/evidence.
- **Fail-closed human approval boundary** — the autonomous path never performs irreversible actions; `RELEASE_AUTHORIZED` requires a positive `ApprovalPort` decision.
- **No autonomous push/merge/deploy** — no release mutation is embedded in the autonomous path.

## Residual Risks

- **Unsandboxed execution of model-authored code** — commands and tests may run with worker privileges, network access, and any inherited credentials.
- **Post-hoc-only escape detection** — `escape_check` runs after execution; a TOCTOU window exists, hard links are not detected, and side effects that already occurred are not undone.
- **No evidence-at-rest integrity verification** — stored evidence is not signed or MACed; the digest is self-asserted.
- **In-memory-only telemetry** — the reference telemetry adapter (`InMemoryTelemetry`) does not persist route records; durable telemetry is not implemented.
