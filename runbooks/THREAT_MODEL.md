# Threat Model

This document describes the **current implemented** security posture of Doom Team. It intentionally avoids overstating protections that are not present.

## Assets

- Candidate evidence and verdicts
- Workspace state and preserved artifacts
- Human approval decisions

## Execution Environment

Model-authored code is **not** executed inside a true OS/container sandbox. We do not claim a sandbox. The following practical controls are implemented and verified by tests:

- **Environment sanitization**: All subprocesses launched by Doom's own code (Git operations via `GitWorkspace`, and any command run through `doom_team.subprocess_guard.run_guarded`) receive a sanitized environment with credential/sensitive variables (e.g., `DOOM_API_KEY`, `AWS_SECRET_ACCESS_KEY`, tokens, passwords) stripped. Arbitrary user-provided callables are responsible for using `run_guarded` if they spawn subprocesses.
- **argv-only subprocess execution**: All subprocesses are launched with argv arrays and `shell=False`; no shell interpolation.
- **Path/symlink boundary checks**: Workspace paths are resolved and checked for symlink components before each Git subprocess execution (pre-execution), in addition to the post-hoc `escape_check`. These checks reduce but do not eliminate the TOCTOU window.
- **Network isolation**: Where the platform supports it (Linux with a working `unshare --net`), subprocesses launched via `run_guarded` run without network access. When `unshare` is unavailable or not permitted, the subprocess runs with the parent network namespace (residual risk documented below).

Workspace isolation remains a logical/process boundary, not a complete security sandbox.

## Threats

- **Stale evidence used to accept a newer candidate** — mitigated by candidate-bound evidence and freshness checks (`evidence_is_fresh`).
- **QA/Reviewer mutation** — validators receive only the candidate and its evidence; no workspace or repository handle is passed to them.
- **Workspace escape** — `GitWorkspace.escape_check` is a **post-hoc detection** mechanism, not enforcement. We additionally perform a **pre-execution** check that rejects workspace paths containing symlink components before any Git subprocess runs. Together these reduce but do not eliminate the TOCTOU window; they cannot prevent arbitrary execution or side effects that already happened. See Residual Risks.
- **Unbounded duplicate work** — `RecoveryController` detects duplicate evidence digests and consumes the bounded semantic budget.
- **Unauthorized release** — the `Supervisor` cannot return `RELEASE_AUTHORIZED` without a positive human `ApprovalPort` decision; no push/merge/deploy is embedded in the autonomous path.
- **Loss of dirty/failed work** — `preserve` retains failed/dirty workspaces before cleanup.

## Compensating Controls

The actual mitigation story relies on the following controls:

- **Isolated per-task workspaces** — each task runs in its own Git-backed directory under a managed base; cross-task contamination is prevented by separation, not by OS sandboxing.
- **Mutation/path boundaries and sensitive-path rejection** — the permission engine restricts `EDIT` to paths within the workspace root and denies dangerous capabilities (`PUSH`, `MERGE`, `DEPLOY`, `DESTRUCTIVE_DB`) unless a human approval is recorded.
- **Command allowlisting (where applicable)** — repository-level operations in `GitWorkspace` are limited to a small set of Git subcommands (e.g., `git apply`); the mission runner accepts an arbitrary `TaskRunner` callable and is therefore not a complete command allowlist.
- **Sanitized child environments** — subprocesses launched by Doom's own code run with credential/sensitive variables stripped (see Execution Environment).
- **argv-only subprocess execution** — no shell interpolation for subprocesses launched by Doom's own code.
- **Pre-execution path/symlink checks** — workspace paths are checked for symlink components before each Git subprocess.
- **Network isolation (best-effort)** — `run_guarded` applies `unshare --net` when the platform supports it.
- **Durable evidence/provenance with candidate binding** — evidence is persisted and bound to a specific candidate (id and commit); freshness checks reject stale evidence.
- **Deterministic validation gates** — the `Supervisor` accepts only candidates with fresh evidence, passing QA, passing Reviewer, and a passing objective check.
- **Independent QA and Reviewer verification** — QA and Reviewer are read-only by construction and operate only on candidate/evidence.
- **Fail-closed human approval boundary** — the autonomous path never performs irreversible actions; `RELEASE_AUTHORIZED` requires a positive `ApprovalPort` decision.
- **No autonomous push/merge/deploy** — no release mutation is embedded in the autonomous path.

## Residual Risks

- **Unsandboxed execution of model-authored code** — commands and tests may run with worker privileges. Child environments are sanitized (credentials stripped) and network isolation is applied when `unshare --net` is available, but arbitrary user-provided callables are not covered and network isolation may be unavailable. No OS/container sandbox exists.
- **Residual escape window** — pre-execution symlink checks and post-hoc `escape_check` reduce but do not eliminate the TOCTOU window; hard links are not detected and side effects that already occurred are not undone.
- **No evidence-at-rest integrity verification** — stored evidence is not signed or MACed; the digest is self-asserted.
- **In-memory-only telemetry** — the reference telemetry adapter (`InMemoryTelemetry`) does not persist route records; durable telemetry is not implemented.
