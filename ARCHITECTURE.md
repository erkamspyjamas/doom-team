# Doom Team — Target Architecture

```text
Human/API/CLI
    ↓
Objective / Mission
    ↓
Task Contract + Policy
    ↓
Planning / DAG
    ↓
Orchestration Port
    ↓
Replaceable Orchestration Adapter
    ↓
Agent execution
    ↓
Candidate + Evidence
    ↓
QA / Reviewer
    ↓
Acceptance / Supervisor
    ↓
Human approval boundary
```

## Doom domain concepts

`Objective`, `Mission`, `TaskContract`, `Attempt`, `RecoveryEpoch`, `Candidate`, `Evidence`, `Capability`, `Permission`, `Acceptance`, `SupervisorDecision`, `ApprovalBoundary`.

Do not leak orchestration-engine-specific types into the domain model.

## Expected ports

Conceptually:

- `OrchestrationPort`
- `WorkspacePort`
- `RepositoryPort`
- `EvidenceStore`
- `InferenceTelemetryPort`
- `ApprovalPort`
- `Clock`

The core should be testable without a live orchestration engine.

## Initial orchestration substrate

Gas City is the preferred initial substrate for durable work, DAG/dependencies, concurrency, dispatch, sessions and generic runtime recovery.

Doom retains engineering trust semantics and integrates Gas City through an adapter.

## Evidence

Evidence is durable and candidate-bound. At minimum it must identify objective, mission, task, attempt, recovery epoch, workspace, baseline commit, candidate commit, producer role/run, command/test artifacts, actual provider/model/runtime, timestamp and digest.

## Acceptance

Distinguish at least conceptually:

`UNASSESSED → CANDIDATE_READY → QA_ACCEPTED → REVIEW_ACCEPTED → OBJECTIVE_ACCEPTED → WAITING_FOR_FINAL_APPROVAL → RELEASE_AUTHORIZED`

Generic orchestration completion must not shortcut these states.

## Recovery

Runtime failures such as provider timeout/429/503/session crash are separate from semantic failures such as QA/Reviewer blockers or repeated acceptance failures.

Runtime retry cannot reset semantic exhaustion.

## Workspace safety

Every mutating attempt uses an isolated reversible workspace. Dirty, failed, uncertain or unpersisted work is preserved before cleanup.

## Human boundary

Routine reversible engineering is autonomous. Irreversible/shared-state actions remain human-gated.
